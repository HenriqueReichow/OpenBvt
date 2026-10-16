"""sonar_node: publishes a sonar (through the opensonar service) on ROS 2: P900/M900, or one of the
.aris family (parameter family: "aris").

Per head N (topics under the node namespace, ~/headN/…):
    image     sensor_msgs/Image mono16      the XY fan (row 0 = farthest), best effort
    raw       opensonar_msgs/RawPing          raw signal + compact configuration, reliable (P900/M900)
    frame     opensonar_msgs/BeamFrame        intensities on a range × beam grid, reliable (.aris family)
    info      opensonar_msgs/SonarInfo        head description, transient_local, on connect and on changes
and ~/status (opensonar_msgs/SonarStatus), ~/events (opensonar_msgs/EventMark), /diagnostics.
header.stamp is the ping's UTC time, corrected for the sonar clock (docs/SYNC.md).
"""
import math
import threading
from array import array
import time

import numpy as np
import rclpy
from rclpy.executors import ExternalShutdownException
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
from opensonar_msgs.msg import BeamFrame, EventMark, RawPing, SonarInfo, SonarStatus, TransducerConfig
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import Image
from std_msgs.msg import Header

from .protocol import Commands, ServiceError, Subscription, start_service

def stamp_of(t):
    """Unix time (s, float) → builtin_interfaces/Time."""
    from builtin_interfaces.msg import Time
    sec = math.floor(t)
    return Time(sec=int(sec), nanosec=int(round((t - sec) * 1e9)) % 1000000000)


def decode_b64(s, dtype):
    import base64
    return np.frombuffer(base64.b64decode(s), dtype)


def transducers_msg(cfg):
    return [TransducerConfig(index=int(t["index"]), high_freq=int(t["high_freq"]), low_freq=int(t["low_freq"]),
                             flags=int(t["flags"]), position=[float(v) for v in t["position"]],
                             rotation=[float(v) for v in t["rotation"]], oriented=bool(t["oriented"]))
            for t in cfg["transducers"]]


def raw_msg(ping, header, cfg):
    m = ping.meta
    out = RawPing(header=header, serial=m["serial"], head=int(m["head"]), number=int(m["number"]), seq=int(m["seq"]),
                  tz_offset=int(m["tz_offset"]), range_start=float(m["range_start"]), range_stop=float(m["range_stop"]),
                  sound_speed=float(m.get("sound_speed") or 0.0), gain=float(m.get("gain") or 0.0),
                  tvg=float(m.get("tvg") or 0.0), range_bins=int(m.get("bins", 0)), beams=int(m.get("beams", 0)),
                  range_resolution=float(m.get("res", 0.0)), bin_range_near=float(m.get("bin_near", 0.0)),
                  bin_range_far=float(m.get("bin_far", 0.0)), config_id=m.get("config_id", ""))
    out.samples = array("I", [int(s.size) for s in ping.signals])
    if ping.signals:
        out.signal = array("H", np.concatenate(ping.signals).astype("<u2").tobytes())
    if ping.signals_q:
        out.signal_q = array("H", np.concatenate(ping.signals_q).astype("<u2").tobytes())
    if cfg and "sample_rate" in cfg:
        out.sample_rate, out.pulse_sample_rate = int(cfg["sample_rate"]), int(cfg["pulse_sample_rate"])
        out.element_spacing, out.mapping_method = float(cfg["element_spacing"]), int(cfg["mapping_method"])
        out.transducers = transducers_msg(cfg)
    return out


def frame_msg(ping, header, cfg):
    """BeamFrame from the R-Theta image of a frame (row 0 = far range there, nearest here)."""
    m = ping.meta
    img = ping.images["rtheta"][0][::-1]
    angles = -np.asarray(cfg.get("beam_angles", []), np.float32)          # opensonar: + right → ROS: + left
    return BeamFrame(header=header, serial=m["serial"], head=int(m["head"]), number=int(m["number"]),
                     seq=int(m["seq"]), sound_speed=float(m.get("sound_speed") or 0.0),
                     gain=float(m.get("gain") or 0.0), samples=int(img.shape[0]), beams=int(img.shape[1]),
                     range_start=float(m["range_start"]), range_resolution=float(m.get("res") or 0.0),
                     beam_angles=angles.tolist(), vertical_fov=0.0,
                     intensity=array("B", np.ascontiguousarray(img, np.uint8).tobytes()))


def image_msg(img, header):
    out = Image(header=header, height=int(img.shape[0]), width=int(img.shape[1]), encoding="mono16",
                is_bigendian=0, step=int(img.shape[1]) * 2)
    # array('B'): assigning bytes to a uint8[] field in Humble's rclpy takes ~200 ms for a 5 MB image
    out.data = array("B", np.ascontiguousarray(img, "<u2").tobytes())
    return out


class SonarNode(Node):
    def __init__(self):
        super().__init__("sonar")
        p = self.declare_parameter
        p("source", "192.168.1.45")            # sonar IP, ip:port (emulator), or a .son / .aris file
        p("family", "")                        # "aris" for a live sonar of the .aris family
        p("system_type", -1)                   # .aris family: 0, 1 or 2; < 0 = from the sonar beacon
        p("heads", [0])
        p("range", [0.0])                      # [stop] or [start, stop] in m for every head; [0] = keep
        p("gain", -1.0)                        # < 0 = keep
        p("tvg", -1.0)
        p("service_host", "127.0.0.1")
        p("rep_port", 4545)
        p("pub_port", 4546)
        p("start_service", True)               # start `opensonar server` if none is running
        p("server_command", "opensonar server")
        p("frame_prefix", "sonar_head")        # frame of head N: <frame_prefix>N
        p("smooth", True)
        p("replay_speed", 1.0)                 # .son source: 1 = recorded pace, 0 = as fast as possible
        p("publish_image", True)
        p("publish_raw", True)
        p("publish_info", True)
        p("publish_diagnostics", True)
        p("image_qos_depth", 5)
        p("raw_qos_depth", 200)
        g = lambda name: self.get_parameter(name).value          # noqa: E731
        self.heads = [int(h) for h in g("heads")]
        self.frame_prefix = g("frame_prefix")
        host = g("service_host")
        self.service_proc = None
        self.cmd = Commands(f"tcp://{host}:{g('rep_port')}")
        if not self.cmd.alive():
            if not g("start_service"):
                raise ServiceError(f"no opensonar service at {host}:{g('rep_port')}")
            self.get_logger().info("starting the opensonar service")
            self.service_proc = start_service(g("server_command"), g("rep_port"), g("pub_port"))
        try:
            self._setup(g, host)
        except Exception:
            self._kill_service()                      # do not leave the service we started behind
            raise

    def _kill_service(self):
        if self.service_proc is not None and self.service_proc.poll() is None:
            try:
                self.cmd.call("quit", timeout=3.0)
                self.service_proc.wait(5)
            except Exception:  # noqa: BLE001
                self.service_proc.kill()

    def _setup(self, g, host):
        self.sub = Subscription(f"tcp://{host}:{g('pub_port')}")
        # publishers
        best = QoSProfile(depth=g("image_qos_depth"), reliability=ReliabilityPolicy.BEST_EFFORT,
                          history=HistoryPolicy.KEEP_LAST)
        reliable = QoSProfile(depth=g("raw_qos_depth"), reliability=ReliabilityPolicy.RELIABLE,
                              history=HistoryPolicy.KEEP_LAST)
        latched = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                             durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.want = {k: bool(g(f"publish_{k}")) for k in ("image", "raw", "info", "diagnostics")}
        self.pubs = {}
        for h in self.heads:
            if self.want["image"]:
                self.pubs[("image", h)] = self.create_publisher(Image, f"~/head{h}/image", best)
            if self.want["raw"]:
                self.pubs[("raw", h)] = self.create_publisher(RawPing, f"~/head{h}/raw", reliable)
                self.pubs[("frame", h)] = self.create_publisher(BeamFrame, f"~/head{h}/frame", reliable)
            if self.want["info"]:
                self.pubs[("info", h)] = self.create_publisher(SonarInfo, f"~/head{h}/info", latched)
        self.status_pub = self.create_publisher(SonarStatus, "~/status", 10)
        self.events_pub = self.create_publisher(EventMark, "~/events", QoSProfile(depth=50))
        self.diag_pub = self.create_publisher(DiagnosticArray, "/diagnostics", 10) if self.want["diagnostics"] else None
        # connect and start
        self.received, self.lost, self._last_seq = 0, 0, None
        self.rates = {h: [] for h in self.heads}
        self.last_info, self.last_meta, self.info_rate = {}, {}, {}
        self.status = {}
        options = {"family": g("family")} if g("family") else {}
        if g("family") and g("system_type") >= 0:
            options["system_type"] = int(g("system_type"))
        self.connect(g("source"), **options)
        self.apply_settings(g("range"), g("gain"), g("tvg"))
        self.frames = self.info.get("sonar_type") == "aris"          # beamformed frames instead of raw signal
        if self.frames:
            for h in self.heads:
                self.pubs.pop(("raw", h), None)
        else:
            for h in self.heads:
                self.pubs.pop(("frame", h), None)
        kinds = (["xy"] if self.want["image"] else []) + (["rtheta"] if self.frames and self.want["raw"] else [])
        speed = None if self.info["live"] or g("replay_speed") <= 0 else float(g("replay_speed"))
        self.cmd.call("start", heads=self.heads, image=kinds or False, smooth=bool(g("smooth")), speed=speed)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._receive, daemon=True)
        self._thread.start()
        self.create_timer(1.0, self._periodic)
        self.get_logger().info(f"publishing heads {self.heads} of {self.info['model']} serial {self.info['serial']}"
                               f" ({'live' if self.info['live'] else 'file'} {self.info['source']})")

    # --- service ---
    def connect(self, source, **options):
        st = self.cmd.call("status")
        if st["connected"] and st["source"] == source:
            self.get_logger().info(f"the service is already connected to {source}")
        else:
            self.cmd.call("connect", source=source, timeout=60.0, **options)
        self.info = self.cmd.call("heads")
        try:
            self.config = {c["head"]: c for c in self.cmd.call("config")["heads"]}
        except ServiceError as e:
            if "unknown command" in str(e):
                raise ServiceError("the opensonar service is too old for opensonar_ros (no `config` command): "
                                   "update the opensonar package, or set server_command") from None
            raise
        self.firmware = "" if self.info.get("firmware") is None else str(self.info["firmware"])
        for h in self.heads:
            if not 0 <= h < len(self.info["heads"]):
                raise ServiceError(f"head {h} does not exist ({len(self.info['heads'])} heads)")

    def apply_settings(self, range_, gain, tvg):
        if not self.info["live"]:
            return
        r = [float(v) for v in range_]
        for h in self.heads:
            lo, hi = self.info["heads"][h]["limits"]
            if len(r) == 1 and r[0] > 0:
                self.cmd.call("range", head=h, start=lo, stop=r[0])
            elif len(r) >= 2 and r[1] > 0:
                self.cmd.call("range", head=h, start=r[0], stop=r[1])
            if gain >= 0:
                self.cmd.call("gain", head=h, value=float(gain))
            if tvg >= 0 and self.info["heads"][h].get("tvg") is not None:
                self.cmd.call("tvg", head=h, value=float(tvg))

    # --- pings ---
    def header(self, h, t):
        return Header(stamp=stamp_of(t), frame_id=f"{self.frame_prefix}{h}")

    def _receive(self):
        while not self._stop.is_set() and rclpy.ok():
            try:
                got = self.sub.recv(0.2)
            except Exception as e:  # noqa: BLE001 — keep receiving
                self.get_logger().error(f"receive: {e}")
                continue
            if got is None:
                continue
            kind, data = got
            try:
                if kind == "event":
                    self._event(data)
                elif data.meta["head"] in self.heads and data.meta["serial"] == self.info["serial"]:
                    self._ping(data)
            except Exception as e:  # noqa: BLE001
                if not rclpy.ok() or self._stop.is_set():   # shutting down (Ctrl+C ends the context first)
                    return
                self.get_logger().error(f"publishing ping: {type(e).__name__}: {e}")

    def _ping(self, ping):
        m = ping.meta
        h = int(m["head"])
        self.received += 1
        if self._last_seq is not None and m["seq"] > self._last_seq + 1:
            self.lost += m["seq"] - self._last_seq - 1
        self._last_seq = m["seq"]
        now = time.time()
        self.rates[h] = [t for t in self.rates[h] if now - t < 5.0] + [now]
        header = self.header(h, m["timestamp"])
        if ("raw", h) in self.pubs:
            self.pubs[("raw", h)].publish(raw_msg(ping, header, self.config.get(h)))
        if ("frame", h) in self.pubs and "rtheta" in ping.images:
            self.pubs[("frame", h)].publish(frame_msg(ping, header, self.config.get(h, {})))
        if ("image", h) in self.pubs and "xy" in ping.images:
            self.pubs[("image", h)].publish(image_msg(ping.images["xy"][0], header))
        key = (round(m["range_start"], 3), round(m["range_stop"], 3), m.get("gain"), m.get("tvg"),
               m.get("bins"), m.get("sound_speed"))
        self.last_meta[h] = m
        if ("info", h) in self.pubs and self.last_info.get(h) != key:
            self.last_info[h] = key
            self.publish_info(h, m)

    def ping_rate(self, h):
        t = self.rates.get(h, [])
        return (len(t) - 1) / (t[-1] - t[0]) if len(t) > 1 and t[-1] > t[0] else 0.0

    def publish_info(self, h, m=None):
        cfg, hd = self.config[h], self.info["heads"][h]
        angles = -np.asarray(cfg.get("beam_angles", []), np.float32)          # opensonar: + right → ROS: + left
        msg = SonarInfo(header=Header(stamp=self.get_clock().now().to_msg(), frame_id=f"{self.frame_prefix}{h}"),
                        serial=self.info["serial"], sonar_model=self.info["model"], firmware=self.firmware, head=h,
                        head_model=hd["model"], center_freq=int(hd.get("center_freq") or 0),
                        beams=int(cfg.get("beams", 0)), beam_angles=angles.tolist(),
                        field_of_view=float(abs(angles[-1] - angles[0])) if angles.size else 0.0,
                        range_min_limit=float(hd["limits"][0]), range_max_limit=float(hd["limits"][1]),
                        config_id=cfg["config_id"], ping_rate=float(self.ping_rate(h)))
        if "sample_rate" in cfg:                    # raw-signal heads: what is needed to reprocess them
            pulse = decode_b64(cfg["pulse"], "<c8")
            msg.sample_rate, msg.pulse_sample_rate = int(cfg["sample_rate"]), int(cfg["pulse_sample_rate"])
            msg.element_spacing, msg.mapping_method = float(cfg["element_spacing"]), int(cfg["mapping_method"])
            msg.transducers = transducers_msg(cfg)
            msg.pulse_real = array("f", pulse.real.astype("<f4").tobytes())
            msg.pulse_imag = array("f", pulse.imag.astype("<f4").tobytes())
            msg.normalization = array("f", decode_b64(cfg["normalization"], "<f4").tobytes())
        if m is not None:
            msg.range_start, msg.range_stop = float(m["range_start"]), float(m["range_stop"])
            msg.range_bins, msg.range_resolution = int(m.get("bins", 0)), float(m.get("res", 0.0))
            msg.bin_range_near, msg.bin_range_far = float(m.get("bin_near", 0.0)), float(m.get("bin_far", 0.0))
            msg.sound_speed, msg.gain, msg.tvg = (float(m.get(k) or 0.0) for k in ("sound_speed", "gain", "tvg"))
        else:
            msg.sound_speed, msg.gain, msg.tvg = float(cfg["sound_speed"]), float(hd.get("gain") or 0), float(hd.get("tvg") or 0)
        self.info_rate[h] = msg.ping_rate
        self.pubs[("info", h)].publish(msg)

    def _event(self, e):
        rec = e.get("recorded_pings")
        self.events_pub.publish(EventMark(header=Header(stamp=stamp_of(e["timestamp"]), frame_id=""),
                                          serial=str(e.get("serial", "")), text=str(e.get("text", "")),
                                          seq=int(e.get("seq", 0)), recorded_pings=-1 if rec is None else int(rec)))

    # --- 1 Hz: status and diagnostics ---
    def _periodic(self):
        try:
            st = self.cmd.call("status", timeout=2.0)
            ok = True
        except ServiceError as e:
            st, ok = {"error": str(e)}, False
        self.status = st
        rec = st.get("record") or {}
        self.status_pub.publish(SonarStatus(
            header=Header(stamp=self.get_clock().now().to_msg()), connected=bool(st.get("connected")),
            live=bool(self.info["live"]), source=str(st.get("source") or ""), serial=str(st.get("serial") or ""),
            running=bool(st.get("running")), heads=[int(h) for h in st.get("heads") or []],
            published=int(st.get("published") or 0), received=self.received, lost=self.lost,
            recording=str(st.get("recording") or ""), recorded=int(rec.get("pings") or st.get("recorded") or 0),
            clock_offset=float(st.get("clock_offset") or 0.0), error=str(st.get("error") or "")))
        if self.diag_pub is not None:
            self.diag_pub.publish(self.diagnostics(ok, st))
        for h in self.heads:                       # info again when the ping rate changed by more than 10 %
            r, last = self.ping_rate(h), self.info_rate.get(h, 0.0)
            if ("info", h) in self.pubs and h in self.last_meta and r > 0 and abs(r - last) > 0.1 * max(last, 1e-6):
                self.publish_info(h, self.last_meta[h])

    def diagnostics(self, ok, st):
        off = float(st.get("clock_offset") or 0.0)
        name = f"opensonar sonar {self.info.get('serial', '')}"
        conn = DiagnosticStatus(name=f"{name}: connection", hardware_id=str(self.info.get("serial", "")))
        if not ok or not st.get("connected"):
            conn.level, conn.message = DiagnosticStatus.ERROR, "service not answering" if not ok else "not connected"
        elif not st.get("running"):
            conn.level, conn.message = DiagnosticStatus.WARN, "connected, not pinging"
        else:
            conn.level, conn.message = DiagnosticStatus.OK, "pinging"
        conn.values = [KeyValue(key="source", value=str(st.get("source"))),
                       KeyValue(key="published", value=str(st.get("published"))),
                       KeyValue(key="received", value=str(self.received))] + \
                      [KeyValue(key=f"head {h} ping rate (Hz)", value=f"{self.ping_rate(h):.2f}") for h in self.heads]
        lost = DiagnosticStatus(name=f"{name}: lost pings", hardware_id=conn.hardware_id,
                                level=DiagnosticStatus.OK if self.lost == 0 else DiagnosticStatus.WARN,
                                message=f"{self.lost} lost", values=[KeyValue(key="lost", value=str(self.lost))])
        clock = DiagnosticStatus(name=f"{name}: sonar clock", hardware_id=conn.hardware_id,
                                 values=[KeyValue(key="offset_s", value=f"{off:.3f}")])
        if abs(off) > 86400:
            clock.level, clock.message = DiagnosticStatus.WARN, f"sonar clock not set ({off / 86400:+.0f} days); stamps corrected"
        else:
            clock.level, clock.message = DiagnosticStatus.OK, f"offset {off:+.3f} s (corrected)"
        return DiagnosticArray(header=Header(stamp=self.get_clock().now().to_msg()), status=[conn, lost, clock])

    # --- shutdown ---
    def close(self):
        self._stop.set()
        self._thread.join(2)
        try:
            if self.service_proc is not None:
                self.cmd.call("quit", timeout=5.0)            # our own service: stop it (closes any recording)
                self.service_proc.wait(10)
            else:
                self.cmd.call("stop", timeout=5.0)            # someone else's service: only stop pinging
        except Exception:  # noqa: BLE001
            if self.service_proc is not None:
                self.service_proc.kill()
        self.sub.close()
        self.cmd.close()


def main(args=None):
    rclpy.init(args=args)
    try:
        node = SonarNode()
    except Exception:
        rclpy.shutdown()
        raise
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):   # Ctrl+C / SIGINT: a normal exit
        pass
    finally:
        node.close()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
