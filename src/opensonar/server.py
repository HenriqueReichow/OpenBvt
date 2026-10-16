"""opensonar service: one process owns the sonar, records and publishes the pings over ZeroMQ.

- PUB (default tcp://127.0.0.1:4546): one multipart message per ping, topic `ping/<serial>/<head>`.
- REP (default tcp://127.0.0.1:4545): JSON commands (discover, connect, heads, range, gain, tvg, start,
  stop, record, stop_record, status, disconnect).

The wire format is in docs/SERVER.md. Recording happens in the acquisition thread, right after each ping
is received; publishing (and rendering the optional XY image) runs in another thread, so neither a slow
client nor the rendering slows the ping rate down or causes a gap in the .son file.
"""
import csv
import json
import os
import queue
import threading
import time
from datetime import datetime, timezone

import numpy as np

from . import sdk
from .son import OpenSonarError

REP_PORT, PUB_PORT = 4545, 4546
VERSION = 1                                   # protocol version, in every message and in `status`


def topic(serial, head):
    return f"ping/{serial}/{head}"


def utc_iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def ping_message(serial, head, ping, seq, image=None, images=None, extra=None):
    """Multipart message of one ping: [topic, metadata JSON, I channels…, Q channels…, images…].

    images: [(kind, u16 image, geometry)] with kind "xy" or "rtheta"; `image` = (xy image, geometry) is
    the older form. extra: more metadata (bins, resolution, gain, TVG, sound speed, config_id).
    """
    meta = {
        "v": VERSION, "serial": serial, "head": head, "seq": seq,
        "number": int(ping.number), "timestamp": float(ping.timestamp), "utc": utc_iso(ping.timestamp),
        "tz_offset": int(ping.tz_offset), "range_start": float(ping.range_start),
        "range_stop": float(ping.range_stop),
        "signals": [int(s.size) for s in ping.signals], "signals_q": [int(s.size) for s in ping.signals_q],
        "dtype": "<u2", "image": None, "images": [],
    }
    meta.update(extra or {})
    parts = [np.ascontiguousarray(s, "<u2").tobytes() for s in list(ping.signals) + list(ping.signals_q)]
    if image is not None:
        images = [("xy",) + tuple(image)] + list(images or [])
    for kind, img, geo in images or ():
        im = {"kind": kind, "rows": int(img.shape[0]), "cols": int(img.shape[1]), "res": float(geo["res"])}
        if kind == "xy":
            im.update(origin_row=int(geo["origin_row"]), origin_col=int(geo["origin_col"]))
            meta["image"] = im
        else:                                  # rtheta: range of the first/last row, bearing of the first/last column
            im.update({k: float(geo[k]) for k in ("range_first", "range_last", "angle_first", "angle_last")})
        meta["images"].append(im)
        parts.append(np.ascontiguousarray(img, "<u2").tobytes())
    return [topic(serial, head).encode(), json.dumps(meta).encode()] + parts


def head_config(cfg):
    """JSON-friendly configuration of a head (son.HeadConfig): what the image generator needs, plus the
    beam angles. Big arrays as base64 little-endian (pulse: complex64, normalization: float32)."""
    import base64
    import hashlib
    from .image import mapping_method, rtheta_geometry
    from .son import Ping
    trs = [{"index": t.index, "high_freq": int(t.high_freq), "low_freq": int(t.low_freq), "flags": int(t.flags),
            "position": [float(v) for v in t.position], "rotation": [float(v) for v in t.rotation],
            "oriented": bool(t.oriented)} for t in cfg.transducers]
    pulse = np.ascontiguousarray(cfg.pulse, "<c8").tobytes()
    norm = np.ascontiguousarray(cfg.normalization, "<f4").tobytes()
    out = {"model": cfg.model, "center_freq": int(cfg.center_freq), "sound_speed": float(cfg.sound_speed),
           "range_min": float(cfg.range_min), "range_max": float(cfg.range_max),
           "sample_rate": int(cfg.raw["u32_a"]), "pulse_sample_rate": int(cfg.raw["u32_b"]),
           "element_spacing": float(cfg.raw["f_a"]), "mapping_method": int(mapping_method(cfg)),
           "transducers": trs, "pulse": base64.b64encode(pulse).decode(),
           "normalization": base64.b64encode(norm).decode()}
    h = hashlib.sha1(json.dumps({k: v for k, v in out.items()}, sort_keys=True).encode())
    out["config_id"] = h.hexdigest()[:16]
    try:                                       # beam angles do not depend on the range
        n = max(len(trs), 1)
        fake = Ping(0, 0.0, 0, cfg.range_min, cfg.range_max, [np.full(4096, 0x800, np.uint16)] * n, [])
        a = rtheta_geometry(cfg, fake)["angles"]
        out.update(beams=int(a.size), beam_angles=[float(x) for x in a])
    except Exception:  # noqa: BLE001
        out.update(beams=0, beam_angles=[])
    return out


def head_info(head):
    """head_config of a raw-signal head; for a head that hands out frames (no configuration), the
    model, range limits, sound speed and beam angles."""
    if head.config is not None:
        return head_config(head.config)
    import hashlib
    lo, hi = head.limits
    angles = [float(a) for a in head._first.beam_angles]
    out = {"model": head.model, "family": head.sonar.sonar_type, "sound_speed": float(head.sound_speed),
           "range_min": float(lo), "range_max": float(hi), "beams": len(angles), "beam_angles": angles,
           "transducers": []}
    out["config_id"] = hashlib.sha1(json.dumps(out, sort_keys=True).encode()).hexdigest()[:16]
    return out


def _lite(kind, geo):
    if kind == "xy":
        return {k: geo[k] for k in ("res", "origin_row", "origin_col")}
    r, a = geo["ranges"], geo["angles"]
    return {"res": float(geo["res"]), "range_first": float(r[0]), "range_last": float(r[-1]),
            "angle_first": float(a[0]), "angle_last": float(a[-1])}


def _render_worker(configs, jobs, results):
    """Child process: renders XY images, so the service's threads never compete with it for the GIL."""
    from .frame import render
    for job in iter(jobs.get, None):
        h, ping, smooth, kinds = job
        try:
            out = []
            for kind in kinds:
                img, geo = render(configs[h], ping, kind, smooth, crop=True)
                out.append((kind, img, _lite(kind, geo)))
            results.put((out, None, None))
        except Exception as e:  # noqa: BLE001 — sent back to the service
            results.put((None, None, f"{type(e).__name__}: {e}"))


class Renderer:
    """XY rendering in a child process (spawned once per connection)."""

    def __init__(self, configs):
        import multiprocessing as mp
        ctx = mp.get_context("spawn")
        self.jobs, self.results = ctx.Queue(), ctx.Queue()
        self.process = ctx.Process(target=_render_worker, args=(list(configs), self.jobs, self.results),
                                   daemon=True)
        self.process.start()

    def render(self, head, ping, smooth=True, kinds=("xy",), timeout=60.0):
        import queue as _queue
        if not self.process.is_alive():
            raise OpenSonarError("the image process stopped")
        self.jobs.put((head, ping, smooth, list(kinds)))
        end = time.time() + timeout
        while True:
            try:
                img, geo, error = self.results.get(timeout=0.5)
                break
            except _queue.Empty:
                if not self.process.is_alive() or time.time() > end:
                    raise OpenSonarError("the image process stopped") from None
        if error:
            raise OpenSonarError(error)
        return img                                   # [(kind, image, geometry)]

    def close(self):
        if self.process.is_alive():
            self.jobs.put(None)
            self.process.join(5)
            if self.process.is_alive():
                self.process.kill()


def _image_record_worker(configs, base, formats, smooth, jobs, results):
    """Child process: renders **every** recorded ping and writes it in the per-ping image formats."""
    from .frame import render
    from .media import ImageSink
    sink = ImageSink(base, formats)
    error = None
    for h, ping in iter(jobs.get, None):
        try:
            img, geo = render(configs[h], ping, "xy", smooth, crop=True)
            sink.write(h, ping, img, geo)
        except Exception as e:  # noqa: BLE001 — reported when the recording stops
            error = f"{type(e).__name__}: {e}"
    sink.close()
    results.put((sum(sink.count.values()), error))


class ImageRecorder:
    """Per-ping image recording (NPY/NPZ/PNG/TIFF, docs/SERVER.md) in a child process."""

    def __init__(self, configs, base, formats, smooth=True):
        import multiprocessing as mp
        ctx = mp.get_context("spawn")
        self.jobs, self.results = ctx.Queue(), ctx.Queue()
        self.queued = 0
        self.process = ctx.Process(target=_image_record_worker,
                                   args=(list(configs), base, list(formats), smooth, self.jobs, self.results),
                                   daemon=True)
        self.process.start()

    def add(self, head, ping):
        self.jobs.put((head, ping))
        self.queued += 1

    def close(self, timeout=600.0):
        """Waits until every queued ping is written. Returns (images written, error or None)."""
        import queue as _queue
        self.jobs.put(None)
        try:
            result = self.results.get(timeout=timeout)
        except _queue.Empty:
            result = (None, "the image recording did not finish")
        self.process.join(5)
        if self.process.is_alive():
            self.process.kill()
        return result


class Server:
    def __init__(self, bind="127.0.0.1", rep_port=REP_PORT, pub_port=PUB_PORT, context=None):
        import zmq
        self.zmq = zmq
        self.ctx = context or zmq.Context.instance()
        self.rep = self.ctx.socket(zmq.REP)
        self.pub = self.ctx.socket(zmq.PUB)
        self.pub.setsockopt(zmq.SNDHWM, 1000)     # a slow subscriber loses pings, the recording does not
        self.rep_port = self._bind(self.rep, bind, rep_port)
        self.pub_port = self._bind(self.pub, bind, pub_port)
        self.sonar = None
        self.serial = None
        self.renderer = None                      # child process for the XY images (started on demand)
        self._acq = self._pubt = None             # acquisition and publishing threads
        self._running = threading.Event()
        self._lock = threading.Lock()             # protects writer, counters and error
        self.writer, self.record_path, self.recorded = None, None, 0
        self.image_recorder, self.formats = None, []
        self.published, self.error, self.heads_on, self.image_kinds = 0, None, [], []
        self.config_ids = {}                      # head → id of its configuration (see `config`)
        self.smooth, self.speed = True, None
        self.record_started, self.events_path, self.marks = None, None, 0
        self._seq = 0                             # ping counter of the PUB socket, never reset
        self._pub_lock = threading.Lock()         # ZeroMQ sockets are not thread-safe
        self._quit = threading.Event()

    @staticmethod
    def _bind(sock, host, port):
        if port == 0:
            return sock.bind_to_random_port(f"tcp://{host}")
        sock.bind(f"tcp://{host}:{port}")
        return port

    # --- acquisition ---
    def _acquire(self, heads, out):
        """Reads, records and queues every ping. Rendering happens in `_publish`, so it never slows the
        ping rate (or the recording) down."""
        previous = None
        try:
            while self._running.is_set():
                for h in heads:
                    if not self._running.is_set():
                        break
                    head = self.sonar.head(h)
                    if head.n_pings is not None and head._next >= head.n_pings:
                        self._running.clear()                  # end of the file
                        break
                    p = head.ping()
                    if self.speed and not self.sonar.live:                     # file at recorded pace
                        if previous is not None:
                            time.sleep(max(0.0, min(p.timestamp - previous, 5.0)) / self.speed)
                        previous = p.timestamp
                    with self._lock:
                        if self.record_path is not None:
                            if self.writer is not None:
                                self.writer.add(p, h)
                            if self.image_recorder is not None:
                                self.image_recorder.add(h, p)
                            self.recorded += 1
                    out.put((h, p))
        except Exception as e:  # noqa: BLE001 — reported through `status`
            with self._lock:
                self.error = f"{type(e).__name__}: {e}"
        finally:
            self._running.clear()
            out.put(None)

    @property
    def with_image(self):
        return bool(self.image_kinds)

    @staticmethod
    def _kinds(image):
        """start/step/image argument: False/True (= XY) or a list of "xy"/"rtheta"."""
        if isinstance(image, str):
            image = [image]
        if image is True:
            return ["xy"]
        kinds = [str(k).lower() for k in (image or [])]
        if set(kinds) - {"xy", "rtheta"}:
            raise OpenSonarError(f"image kinds are xy and rtheta (got {kinds})")
        return list(dict.fromkeys(kinds))

    def _extra(self, h, p):
        """Per-ping metadata besides the signal: R-Theta bins and resolution (they change with the range),
        gain, TVG, sound speed and the configuration id."""
        from .image import rtheta_geometry
        head = self.sonar.head(h)
        cfg = head.config
        if cfg is None:                                        # a frame: its grid is the R-Theta image
            return {"gain": head.gain, "tvg": None, "sound_speed": float(p.sound_speed),
                    "config_id": self.config_ids.get(h, ""), "bins": int(p.n_samples), "beams": int(p.n_beams),
                    "res": p.res, "bin_near": p.range_start, "bin_far": p.range_stop}
        out = {"gain": float(cfg.gain), "tvg": float(cfg.tvg_slope), "sound_speed": float(cfg.sound_speed),
               "config_id": self.config_ids.get(h, "")}
        try:
            g = rtheta_geometry(cfg, p)
            out.update(bins=int(g["rows"]), beams=int(g["cols"]), res=float(g["res"]),
                       bin_near=float(g["ranges"][-1]), bin_far=float(g["ranges"][0]))   # R-Theta row ranges
        except Exception:  # noqa: BLE001 — a ping without samples
            out.update(bins=0, beams=0, res=0.0, bin_near=0.0, bin_far=0.0)
        return out

    def _send(self, h, p, render):
        """Publishes one ping (only one thread at a time: the publisher, or the command loop when stopped)."""
        images = None
        if render:
            try:
                if self.renderer is None:
                    self.renderer = Renderer([hd.config for hd in self.sonar.heads])
                images = self.renderer.render(h, p, self.smooth, self.image_kinds)
            except Exception as e:  # noqa: BLE001 — reported through `status`; the ping still goes out
                with self._lock:
                    self.error = f"image: {type(e).__name__}: {e}"
        msg = ping_message(self.serial, h, p, self._seq, images=images, extra=self._extra(h, p))
        with self._pub_lock:
            self.pub.send_multipart(msg, copy=False)
            self._seq += 1
        with self._lock:
            self.published += 1

    def _publish(self, pending):
        """Publishes every queued ping in order. It takes the whole queue at once and renders the XY image
        only for the newest ping of each head in it: if rendering falls behind, the older pings go out
        without an image, and the screen still gets the latest one."""
        done = False
        while not done:
            batch = [pending.get()]
            while True:
                try:
                    batch.append(pending.get_nowait())
                except queue.Empty:
                    break
            if batch[-1] is None:
                batch.pop()
                done = True
            newest = {h: k for k, (h, _) in enumerate(batch)}
            for k, (h, p) in enumerate(batch):
                self._send(h, p, self.with_image and newest[h] == k)

    def _stop_acquisition(self):
        self._running.clear()
        for t in (self._acq, self._pubt):
            if t is not None:
                t.join(timeout=15)
        self._acq = self._pubt = None

    # --- commands ---
    def cmd_discover(self, timeout=2.0, probe=True):
        """Discovery; with `probe`, connects briefly to each sonar to add Model, Serial and Heads."""
        from . import aris_net
        from .formats.aris import SYSTEM_TYPES
        from .net import NetSonar, discover
        beacons = []

        def listen():                                          # sonars of the .aris family announce themselves
            try:
                beacons.extend(aris_net.discover(float(timeout)))
            except OSError:                                    # beacon port in use
                pass
        listener = threading.Thread(target=listen, daemon=True)
        listener.start()
        found = discover(float(timeout))
        for a in found if probe else ():
            host = f"{a['Host']}:{a.get('Port', 1149)}"
            current = self.sonar is not None and self.sonar.live and str(self.sonar.source) in (host, a["Host"])
            try:
                if current:                                    # already connected: do not open a second link
                    s = self.sonar
                    a.update(Model=s.model, Serial=self.serial, Heads=[h.model for h in s.heads])
                else:
                    with NetSonar(host, timeout=3.0, sync_clock=False) as s:
                        a.update(Model=s.name or (s.heads[0].model if s.heads else ""),
                                 Serial=str(s.serial or ""), Heads=[h.model for h in s.heads])
            except (OpenSonarError, OSError) as e:
                a["ProbeError"] = str(e)
        listener.join()
        for b in beacons:
            found.append({"Host": b["host"], "Port": aris_net.COMMAND_PORT, "Family": "aris",
                          "Model": SYSTEM_TYPES.get(b["system_type"], "?"), "Serial": str(b["serial"]),
                          "SystemType": b["system_type"], "Busy": b["busy"]})
        return {"sonars": found}

    def cmd_connect(self, source=None, **options):
        self.cmd_disconnect()
        self.sonar = sdk.open(source, **options)
        self.serial = str(self.sonar.serial or "0")
        self.error = None
        self.config_ids = {h.index: head_info(h)["config_id"] for h in self.sonar.heads}
        return self.cmd_heads()

    def cmd_disconnect(self):
        self._stop_acquisition()
        self.cmd_stop_record()
        if self.sonar is not None:
            self.sonar.close()
        if self.renderer is not None:
            self.renderer.close()
        self.sonar, self.serial, self.renderer = None, None, None
        return {}

    def _need_sonar(self):
        if self.sonar is None:
            raise OpenSonarError("not connected (use connect)")
        return self.sonar

    def cmd_heads(self):
        s = self._need_sonar()
        return {"source": str(s.source), "live": s.live, "model": s.model, "serial": self.serial,
                "firmware": getattr(s, "firmware", None), "sonar_type": getattr(s, "sonar_type", None),
                "heads": [{"index": h.index, "model": h.model, "limits": list(h.limits),
                           "transducers": len(h.transducers), "gain": h.gain, "tvg": h.tvg,
                           "center_freq": h.center_freq, "n_pings": h.n_pings} for h in s.heads]}

    def cmd_range(self, head=0, start=None, stop=None):
        h = self._need_sonar().head(int(head))
        h.adjust_range(h.limits[0] if start is None else float(start), float(stop))
        return {}

    def cmd_gain(self, head=0, value=None):
        self._need_sonar().head(int(head)).adjust_gain(float(value))
        return {}

    def cmd_tvg(self, head=0, value=None):
        self._need_sonar().head(int(head)).adjust_tvg(float(value))
        return {}

    def cmd_start(self, heads=(0,), image=False, smooth=True, speed=None):
        s = self._need_sonar()
        heads = [int(h) for h in ([heads] if isinstance(heads, int) else heads)]
        for h in heads:
            s.head(h)                                          # validates the index
        self._stop_acquisition()
        self.heads_on, self.image_kinds, self.error = heads, self._kinds(image), None
        self.smooth, self.speed = bool(smooth), (float(speed) if speed else None)
        self._running.set()
        q = queue.Queue()
        self._acq = threading.Thread(target=self._acquire, args=(heads, q), daemon=True)
        self._pubt = threading.Thread(target=self._publish, args=(q,), daemon=True)
        self._pubt.start()
        self._acq.start()
        return {"heads": heads}

    def cmd_image(self, enabled=True, smooth=True, kinds=None):
        """Turns the published images (kinds: xy and/or rtheta; default xy) and their 3x3 smoothing on/off,
        also while running."""
        self.image_kinds, self.smooth = (self._kinds(kinds or True) if enabled else []), bool(smooth)
        return {}

    def cmd_stop(self):
        self._stop_acquisition()
        return {}

    def cmd_config(self, head=None):
        """Full configuration of each head (or of one), enough to rebuild the images from the raw signal
        with opensonar (`opensonar.son.config_from_dict`): see docs/SERVER.md."""
        s = self._need_sonar()
        heads = s.heads if head is None else [s.head(int(head))]
        return {"heads": [dict(head_info(h), head=h.index) for h in heads]}

    # --- playback of a file ---
    def _file_head(self):
        s = self._need_sonar()
        if s.live:
            raise OpenSonarError("only a file can be positioned (seek/step)")
        heads = self.heads_on or [0]
        return s.head(heads[0])

    def cmd_seek(self, index):
        """Moves every head of the file to ping `index` (the next one played or stepped)."""
        h = self._file_head()
        running = self._running.is_set()
        if running:
            self._stop_acquisition()
        i = int(index)
        for hd in self.sonar.heads:
            if hd.n_pings:
                hd._next = max(0, min(i, hd.n_pings - 1))
        if running:
            self.cmd_start(self.heads_on, self.image_kinds, self.smooth, self.speed)
        return {"position": h._next}

    def cmd_step(self, delta=1, image=None, smooth=None):
        """Stops playback and publishes one ping: delta=1 the next one, -1 the previous one.
        image/smooth: as in `start` (default: keep the current choice)."""
        h = self._file_head()
        self._stop_acquisition()
        i = h._next - 1 + int(delta)                  # _next − 1 is the ping on screen
        if not 0 <= i < h.n_pings:
            raise OpenSonarError(f"no ping {i} (0..{h.n_pings - 1})")
        if image is not None:
            self.image_kinds = self._kinds(image)
        if smooth is not None:
            self.smooth = bool(smooth)
        p = h.ping(i)
        self._send(h.index, p, self.with_image)
        return {"position": h._next, "number": int(p.number)}

    # --- event marks ---
    def cmd_mark(self, text=""):
        """Event mark: UTC time + text, published on `event/<serial>` and, while recording, appended to
        <recording>.events.csv (next to the .son)."""
        s = self._need_sonar()
        t = time.time()
        mark = {"v": VERSION, "serial": self.serial, "utc": utc_iso(t), "timestamp": t, "text": str(text),
                "seq": self._seq, "recorded_pings": self.recorded if self.record_path else None,
                "source": str(s.source)}
        with self._lock:
            path = self.events_path if self.record_path else None
            if path:
                new = not os.path.exists(path)
                with open(path, "a", newline="") as f:
                    w = csv.writer(f)
                    if new:
                        w.writerow(["utc", "timestamp", "recorded_pings", "seq", "text"])
                    w.writerow([mark["utc"], f"{t:.6f}", mark["recorded_pings"], mark["seq"], mark["text"]])
                self.marks += 1
        with self._pub_lock:
            self.pub.send_multipart([f"event/{self.serial}".encode(), json.dumps(mark).encode()])
        return {"mark": mark, "file": path}

    def cmd_record(self, path, formats=("son",)):
        """Records every ping. formats: "son" (the lossless recording at `path`: .son, or .aris for a sonar
        of that family) and/or the per-ping XY images "npy", "npz", "png", "tiff"
        (folders <path without extension>_<fmt>/h<head>/ with index.csv)."""
        from .media import IMAGE_FORMATS
        from .writer import raw_writer
        s = self._need_sonar()
        formats = [str(f).lower() for f in ([formats] if isinstance(formats, str) else formats)]
        unknown = set(formats) - {"son"} - set(IMAGE_FORMATS)
        if unknown or not formats:
            raise OpenSonarError(f"formats must be some of son, {', '.join(IMAGE_FORMATS)} (got {formats})")
        path = str(path)
        base = os.path.splitext(path)[0] if os.path.splitext(path)[1].lower() in (".son", ".aris") else path
        with self._lock:
            if self.record_path is not None:
                raise OpenSonarError(f"already recording to {self.record_path}")
            self.writer, raw_path = raw_writer(base, s) if "son" in formats else (None, None)
            images = [f for f in formats if f in IMAGE_FORMATS]
            self.image_recorder = ImageRecorder([h.config for h in s.heads], base, images, self.smooth) \
                if images else None
            self.record_path, self.recorded, self.formats = raw_path or base, 0, formats
            self.record_started, self.marks = time.time(), 0
            self.events_path = base + ".events.csv"
        return {"path": self.record_path, "formats": formats}

    def cmd_stop_record(self):
        with self._lock:
            w, rec, path, n, marks = self.writer, self.image_recorder, self.record_path, self.recorded, self.marks
            events = self.events_path if marks else None
            formats = self.formats
            self.writer, self.image_recorder, self.record_path = None, None, None
        if w is not None:
            w.close()
        images, error = rec.close() if rec is not None else (0, None)
        if error:
            with self._lock:
                self.error = f"image recording: {error}"
        size = os.path.getsize(path) if path and os.path.isfile(path) else 0
        return {"path": path, "pings": n, "bytes": size, "marks": marks, "events": events,
                "formats": formats, "images": images, "error": error}

    def cmd_status(self):
        rec = None
        with self._lock:
            if self.record_path is not None:
                rec = {"path": self.record_path, "pings": self.recorded, "marks": self.marks,
                       "formats": self.formats, "elapsed": time.time() - self.record_started,
                       "bytes": os.path.getsize(self.record_path) if os.path.isfile(self.record_path) else 0}
        playback = None
        if self.sonar is not None and not self.sonar.live:
            h = self.sonar.head((self.heads_on or [0])[0])
            playback = {"position": h._next, "n_pings": h.n_pings}
        with self._lock:
            return {"record": rec, "playback": playback,"version": VERSION, "connected": self.sonar is not None,
                    "source": None if self.sonar is None else str(self.sonar.source), "serial": self.serial,
                    "running": self._running.is_set() or bool(self._pubt and self._pubt.is_alive()), "heads": self.heads_on, "image": self.with_image, "images": self.image_kinds,
                    "smooth": self.smooth, "speed": self.speed,
                    "clock_offset": None if self.sonar is None else float(getattr(self.sonar, "clock_offset", 0.0)),
                    "published": self.published, "recording": self.record_path,
                    "recorded": self.recorded, "error": self.error,
                    "rep_port": self.rep_port, "pub_port": self.pub_port}

    def cmd_quit(self):
        self._quit.set()
        return {}

    def handle(self, request):
        """Runs one command. Request {"cmd": name, ...args}; reply {"ok": true, ...} or {"ok": false, "error"}."""
        try:
            req = json.loads(request)
            name = req.pop("cmd")
            fn = getattr(self, f"cmd_{name}", None)
            if fn is None:
                raise OpenSonarError(f"unknown command: {name!r}")
            return {"ok": True, **fn(**req)}
        except (OpenSonarError, OSError, ValueError, TypeError, KeyError) as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    # --- loop ---
    def serve(self, until=None):
        """Answers commands until `quit`, Ctrl+C or the `until` event."""
        poller = self.zmq.Poller()
        poller.register(self.rep, self.zmq.POLLIN)
        try:
            while not self._quit.is_set() and not (until is not None and until.is_set()):
                if poller.poll(100):
                    reply = self.handle(self.rep.recv())
                    self.rep.send(json.dumps(reply, default=str).encode())
        except KeyboardInterrupt:
            pass
        finally:
            self.close()

    def close(self):
        self.cmd_disconnect()
        self.rep.close(0)
        self.pub.close(0)


def run(source=None, bind="127.0.0.1", rep_port=REP_PORT, pub_port=PUB_PORT, heads=None, image=False,
        record=None, out=print):
    """`opensonar server`: optionally connects, records and starts right away, then serves commands."""
    srv = Server(bind, rep_port, pub_port)
    out(f"opensonar server: commands tcp://{bind}:{srv.rep_port}, pings tcp://{bind}:{srv.pub_port}")
    if source is not None:
        info = srv.cmd_connect(source)
        out(f"connected: {info['model']} serial {info['serial']}, {len(info['heads'])} head(s)")
        if record:
            srv.cmd_record(record)
            out(f"recording to {record}")
        srv.cmd_start(heads or [0], image)
        out(f"pinging head(s) {heads or [0]}")
    srv.serve()
    out("server stopped")
