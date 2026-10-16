"""opensonar's single API: sonar → heads → pings, the same for a .son file, a real sonar and the emulator.

    import opensonar
    with opensonar.open("samples/exp1.son") as s:        # or "192.168.1.45", or None (discovery)
        head = s.heads[0]
        for ping in head.pings(n=10):
            img, geo = head.image(ping, "xy")

Live, `head.adjust_range(1, 20)`, `head.adjust_gain(...)`, `head.adjust_tvg(...)` change the sonar,
and `head.stream()` fetches pings in a thread and always hands out the latest one.
"""
import os
import threading
import time

from . import frame as _frame
from . import image as _image
from .son import OpenSonarError, SonFile


class ApiError(OpenSonarError):
    pass


class NoPing(ApiError):
    """`Head.ping(timeout=…)` on a live sonar: nothing arrived in time."""


def open(source=None, **options):  # noqa: A001 — opensonar.open is the public entry point
    """Opens a sonar.

    source: path of a .son file; "ip" or "ip:port" of a sonar on the network (real or emulated);
    None = the first sonar that answers multicast discovery.
    options (network): timeout, sync_clock, discovery_timeout.
    options (.ddf file): sound_speed (m/s; these files do not record it, default 1457).
    family="aris": a live sonar that records .aris files (options: system_type, timeout, command_port,
    beacon_port, frame_ip); with source=None, the first one whose beacon is heard.
    """
    if options.pop("family", None) == "aris":
        return LiveArisSonar(source, **options)
    if source is None:
        from .net import discover
        found = discover(options.pop("discovery_timeout", 2.0))
        if not found:
            raise ApiError("no sonar answered discovery")
        a = found[0]
        source = f"{a['Host']}:{a.get('Port', 1149)}"
    source = os.fspath(source)
    ext = os.path.splitext(source)[1].lower()
    if ext in FILE_FORMATS:
        file_options = {"sound_speed": options["sound_speed"]} if options.get("sound_speed") else {}
        return FILE_FORMATS[ext](source, **file_options)
    if os.path.exists(source):
        raise ApiError(f"unknown file format: {ext or source!r} (known: {', '.join(sorted(FILE_FORMATS))})")
    return LiveSonar(source, **options)


class Head:
    """One sonar head. `config` is the configuration in the .son reader format (opensonar.son.HeadConfig)."""

    def __init__(self, sonar, index, config):
        self.sonar, self.index, self.config = sonar, index, config
        self._next = 0

    # --- configuration ---
    @property
    def model(self):
        return self.config.model

    @property
    def transducers(self):
        return self.config.transducers

    @property
    def limits(self):
        """(minimum range, maximum range) in m."""
        return self.config.range_min, self.config.range_max

    @property
    def gain(self):
        return self.config.gain

    @property
    def tvg(self):
        return self.config.tvg_slope

    @property
    def sound_speed(self):
        return self.config.sound_speed

    @property
    def center_freq(self):
        return self.config.center_freq

    @property
    def n_pings(self):
        """Number of pings in the file; None when live."""
        return self.sonar._n_pings(self.index)

    def adjust_range(self, start, stop):
        self.sonar._adjust(self.index, "range", (start, stop))

    def adjust_gain(self, gain):
        self.sonar._adjust(self.index, "gain", gain)

    def adjust_tvg(self, tvg):
        self.sonar._adjust(self.index, "tvg", tvg)

    # --- pings ---
    def ping(self, i=None, timeout=None):
        """File: ping i (default: the next one, in sequence). Live: a new ping (i does not apply).
        timeout (live sonars that stream frames): s to wait, then NoPing; None = wait for one."""
        if i is None:
            i = self._next
        p = self.sonar._ping(self.index, i, timeout) if timeout is not None else self.sonar._ping(self.index, i)
        self._next = i + 1
        return p

    def pings(self, start=None, n=None):
        """Iterator. File: from ping `start` (default: the next one) to the end. Live: endless."""
        if start is not None:
            self._next = start
        k = 0
        while n is None or k < n:
            total = self.n_pings
            if total is not None and self._next >= total:
                return
            yield self.ping()
            k += 1

    def stream(self, real_time=False, on_ping=None, on_demand=None):
        """Fetches pings in a thread; `Stream.latest()` always returns the most recent one.

        On a file, `real_time=True` follows the recorded interval between pings.
        `on_ping(ping)` is called in the thread for **every** ping (lossless recording).
        `on_demand` (default: when there is no `on_ping`): only requests a new ping after the previous
        one was read.
        """
        return Stream(self, real_time, on_ping, on_demand)

    # --- images ---
    def image(self, ping, kind="xy", smooth=True, crop=False):
        """(u16 image, geometry) of the ping. kind: "xy" (fan) or "rtheta".
        smooth=False skips the 3x3 mean; crop=True (XY only) renders just the window within the
        ping's range, which is much faster on long-range heads used at short range."""
        render = {"xy": _image.xy, "rtheta": _image.rtheta}.get(kind)
        if render is None:
            raise ApiError(f"unknown image kind: {kind!r}")
        if kind == "xy":
            return render(self.config, ping, smooth, crop)
        return render(self.config, ping, smooth)

    def frame(self, ping):
        """The ping as a common `opensonar.frame.Frame` (intensities on a range × beam angle grid)."""
        img, geo = _image.rtheta(self.config, ping)
        return _frame.from_rtheta(ping, img, geo, self.sound_speed)

    def __repr__(self):
        lo, hi = self.limits
        return f"<Head {self.index} {self.model} {lo:.1f}–{hi:.1f} m, {len(self.transducers)} transducers>"


class Sonar:
    """Common base. Subclasses implement _ping, _n_pings, _adjust and close."""
    live = False

    def __init__(self, source):
        self.source = source
        self.heads = []

    def head(self, i=0):
        if not 0 <= i < len(self.heads):
            raise ApiError(f"head {i} does not exist (there are {len(self.heads)})")
        return self.heads[i]

    @property
    def model(self):
        return self.name or (self.heads[0].model if self.heads else "")

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def __repr__(self):
        mode = "live" if self.live else "file"
        return f"<Sonar {mode} {self.source}: {self.model}, serial {self.serial}, {len(self.heads)} head(s)>"


class FileSonar(Sonar):
    """A .son recording (read-only)."""

    def __init__(self, path):
        super().__init__(path)
        self._son = SonFile(path)
        s = self._son
        self.sonar_type, self.name, self.serial, self.firmware = s.sonar_type, s.name, s.serial, s.firmware
        self.fpga = s.fpga
        self.clock_offset = 0.0
        self.heads = [Head(self, i, c) for i, c in enumerate(s.heads)]

    def _n_pings(self, head):
        return self._son.n_pings(head)

    def _ping(self, head, i):
        if not 0 <= i < self._son.n_pings(head):
            raise ApiError(f"ping {i} out of range 0..{self._son.n_pings(head) - 1}")
        return self._son.ping(i, head)

    def _adjust(self, head, what, value):
        raise ApiError(f"{what} can only be adjusted live; the file is read-only")


class LiveSonar(Sonar):
    """A sonar on the network (real or emulated), through the native driver (opensonar.net)."""
    live = True

    def __init__(self, host, **options):
        super().__init__(host)
        from .net import NetSonar
        self._net = NetSonar(host, **options)
        r = self._net
        self.sonar_type, self.name, self.serial, self.firmware = r.sonar_type, r.name, r.serial, r.firmware
        self.fpga = r.fpga
        self.heads = [Head(self, i, c) for i, c in enumerate(r.heads)]
        self._lock = threading.Lock()        # one request at a time on the connection

    @property
    def clock_offset(self):
        """Sonar clock − local clock (s), measured on connection; already removed from ping times."""
        return self._net.clock_offset

    def _n_pings(self, head):
        return None

    def _ping(self, head, i):
        with self._lock:
            return self._net.ping(head)

    def _adjust(self, head, what, value):
        lo, hi = self.heads[head].limits
        with self._lock:
            if what == "range":
                start, stop = value
                self._net.adjust_range(head, max(float(start), lo), min(float(stop), hi))
            elif what == "gain":
                self._net.adjust_gain(head, value)
            elif what == "tvg":
                self._net.adjust_tvg(head, value)

    def close(self):
        self._net.close()


class FrameHead(Head):
    """Head of a sonar that hands out beamformed frames: its pings already are `Frame`s."""

    def __init__(self, sonar, index, model, first):
        super().__init__(sonar, index, None)
        self._model, self._first = model, first

    model = property(lambda self: self._model)
    transducers = property(lambda self: [])
    @property
    def limits(self):
        """Live: what the sonar timing allows; file: the recorded window."""
        if hasattr(self.sonar, "_limits"):
            return self.sonar._limits()
        return float(self._first.ranges[0]), float(self._first.ranges[-1])
    tvg = center_freq = property(lambda self: None)
    gain = property(lambda self: getattr(self.sonar, "gain", None))
    sound_speed = property(lambda self: self._first.sound_speed)

    def frame(self, ping):
        return ping

    def image(self, ping, kind="xy", smooth=True, crop=False):
        """(image, geometry) of the frame. kind: "xy" (fan) or "rtheta" (row 0 = far range).
        smooth and crop do not apply: the frame is already beamformed and holds only its window."""
        if kind not in ("xy", "rtheta"):
            raise ApiError(f"unknown image kind: {kind!r}")
        return _frame.render(None, ping, kind)

    def __repr__(self):
        lo, hi = self.limits
        return f"<Head {self.index} {self.model} {lo:.1f}–{hi:.1f} m, {self._first.n_beams} beams>"


class FrameFileSonar(Sonar):
    """A recording of beamformed frames (read-only, one head); subclasses set the reader."""
    sonar_type = None

    def _reader(self, path):
        raise NotImplementedError

    def __init__(self, path):
        super().__init__(path)
        self._file = self._reader(path)
        f = self._file
        self.name, self.serial, self.firmware, self.fpga = f.model, f.serial, "", ""
        self.clock_offset = 0.0
        self.heads = [FrameHead(self, 0, f.model, f.frame(0))]

    def _n_pings(self, head):
        return self._file.n_frames

    def _ping(self, head, i):
        if not 0 <= i < self._file.n_frames:
            raise ApiError(f"ping {i} out of range 0..{self._file.n_frames - 1}")
        return self._file.frame(i)

    def _adjust(self, head, what, value):
        raise ApiError(f"{what} can only be adjusted live; the file is read-only")

    def close(self):
        self._file.close()


class ArisFileSonar(FrameFileSonar):
    """A .aris recording."""
    sonar_type = "aris"

    def _reader(self, path):
        from .formats.aris import ArisFile
        return ArisFile(path)


class DdfFileSonar(FrameFileSonar):
    """A .ddf recording. The files do not record the sound speed: `sound_speed` (m/s) sets it."""
    sonar_type = "ddf"

    def __init__(self, path, sound_speed=None):
        self._sound_speed = sound_speed
        super().__init__(path)

    def _reader(self, path):
        from .formats.ddf import NOMINAL_SOUND_SPEED, DdfFile
        return DdfFile(path, self._sound_speed or NOMINAL_SOUND_SPEED)


class LiveArisSonar(Sonar):
    """A live sonar of the .aris family (opensonar.aris_net), one head whose pings are `Frame`s."""
    live = True

    def __init__(self, host=None, **options):
        from . import aris_net
        if host is None:
            found = aris_net.discover(options.pop("discovery_timeout", 2.0),
                                      options.get("beacon_port", aris_net.BEACON_PORT))
            if not found:
                raise ApiError("no .aris sonar beacon heard")
            host = found[0]["host"]
            options.setdefault("system_type", found[0]["system_type"])
        super().__init__(host)
        self._net = aris_net.ArisNet(host, **options)
        r = self._net
        if not r.serial:                   # without a beacon the serial comes in the frame headers
            r.wait_first(2.0)
        first = r.nominal_frame()          # geometry of the settings; frames may take a while (or never come)
        self.sonar_type, self.name, self.serial, self.firmware, self.fpga = "aris", r.model, r.serial, "", ""
        self.clock_offset = 0.0
        self.heads = [FrameHead(self, 0, r.model, first)]
        self._lock = threading.Lock()

    @property
    def gain(self):
        return self._net.settings["receiver_gain"]

    @property
    def warning(self):
        """Last connection problem (dropped and reconnected…), or None."""
        return self._net.warning

    def link_status(self):
        """Frames received, seconds since the last one, reconnections, warning."""
        return self._net.link_status()

    def drop_queued(self):
        self._net.drop_queued()

    def _limits(self):
        return tuple(float(v) for v in self._net.limits())

    def _n_pings(self, head):
        return None

    def _ping(self, head, i, timeout=None):
        from .aris_net import NoFrame
        try:
            return self._net.frame(timeout)
        except NoFrame as e:
            raise NoPing(str(e)) from None

    def _adjust(self, head, what, value):
        with self._lock:
            if what == "range":
                lo, hi = self._net.limits()
                start, stop = value
                self._net.adjust_range(max(float(start), lo), min(float(stop), hi))
            elif what == "gain":
                self._net.adjust_gain(value)
            else:
                raise ApiError(f"{what} cannot be adjusted on this sonar")

    def close(self):
        self._net.close()


# file extension → Sonar class; each sonar family registers its recording format here
FILE_FORMATS = {".son": FileSonar, ".aris": ArisFileSonar, ".ddf": DdfFileSonar}


class Stream:
    """Continuous pings in a thread. Keeps only the latest one (the consumer never falls behind)."""

    def __init__(self, head, real_time=False, on_ping=None, on_demand=None):
        self.head, self.real_time, self.on_ping = head, real_time, on_ping
        # with nobody needing every ping, the thread waits for the screen to consume before requesting
        # the next one: it does not compete with drawing for the interpreter (live pings are on request)
        self.on_demand = on_ping is None if on_demand is None else on_demand
        self.received = 0
        self.error = None
        self._latest = None
        self._new = threading.Condition()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        previous = None
        try:
            for p in self.head.pings():
                if self._stop.is_set():
                    break
                if self.real_time and previous is not None:
                    time.sleep(max(0.0, min(p.timestamp - previous, 5.0)))
                previous = p.timestamp
                if self.on_ping:
                    self.on_ping(p)
                with self._new:
                    self._latest = p
                    self.received += 1
                    self._new.notify_all()
                    if self.on_demand:                   # only request the next one once this is consumed
                        while self._latest is not None and not self._stop.is_set():
                            self._new.wait(0.2)
        except Exception as e:  # noqa: BLE001 — passed on to the consumer
            self.error = e
        finally:
            self._stop.set()
            with self._new:
                self._new.notify_all()

    @property
    def active(self):
        return not self._stop.is_set()

    def latest(self, timeout=None, only_new=True):
        """Returns the most recent ping. only_new: wait for a ping that has not been handed out yet."""
        with self._new:
            if only_new:
                end = None if timeout is None else time.time() + timeout
                while self._latest is None and not self._stop.is_set():
                    if not self._new.wait(None if end is None else max(0.0, end - time.time())):
                        return None
            p, self._latest = self._latest, (None if only_new else self._latest)
            self._new.notify_all()
        if p is None and self.error:
            raise self.error
        return p

    def __iter__(self):
        while True:
            p = self.latest()
            if p is None:
                return
            yield p

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=15)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.stop()
