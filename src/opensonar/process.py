"""Acquisition in a separate process, so the window does not compete with networking and recording for
the interpreter.

The child process opens the source (sonar, emulator or .son), applies the settings, records every ping
(.son/NPZ) and keeps the latest one. The window asks for a ping when it is ready to draw; only that one
crosses the pipe. `RemoteSonar` mimics the `Sonar`/`Head`/`Stream` interface the viewer uses.
"""
import multiprocessing as mp
import os
import time

from . import image as _image


def _worker(conn, source, head, settings, record, npz, real_time):
    from .sdk import open as open_sonar
    from .writer import SonWriter
    writer = None
    n = 0
    try:
        with open_sonar(source) as s:
            h = s.head(head)
            if settings.get("range"):
                r = settings["range"]
                h.adjust_range(*((h.limits[0], r[0]) if len(r) == 1 else r))
            if settings.get("gain") is not None:
                h.adjust_gain(settings["gain"])
            if settings.get("tvg") is not None:
                h.adjust_tvg(settings["tvg"])
            conn.send(("info", dict(live=s.live, model=s.model, sonar_type=s.sonar_type, serial=s.serial,
                                    firmware=s.firmware, clock_offset=s.clock_offset,
                                    configs=[c.config for c in s.heads])))
            writer = SonWriter.for_sonar(record, s) if record else None
            if npz:
                os.makedirs(npz, exist_ok=True)
            wants, pending, previous = False, None, None
            for p in h.pings():
                if real_time and previous is not None:
                    time.sleep(max(0.0, min(p.timestamp - previous, 5.0)))
                previous = p.timestamp
                if writer:
                    writer.add(p, head)
                if npz:
                    from .cli import _save
                    img, geo = h.image(p, "xy")
                    _save(img, geo, "xy", "npz", os.path.join(npz, f"ping_{n:05d}.npz"), p)
                n += 1
                pending = p
                stop = False
                while conn.poll(0):
                    cmd = conn.recv()
                    if cmd[0] == "want":
                        wants = True
                    elif cmd[0] == "range" and s.live:
                        h.adjust_range(*cmd[1:])
                    elif cmd[0] == "stop":
                        stop = True
                if wants and pending is not None:
                    conn.send(("ping", pending, n))
                    wants, pending = False, None
                if stop:
                    break
            if pending is not None:                          # the last ping of the file
                conn.send(("ping", pending, n))
            conn.send(("end", n))
    except Exception as e:  # noqa: BLE001 — passed on to the window
        try:
            conn.send(("error", f"{type(e).__name__}: {e}"))
        except (OSError, EOFError):
            pass
    finally:
        if writer:
            writer.close()
        try:
            conn.send(("closed", n))
        except (OSError, EOFError):
            pass


class RemoteStream:
    """Same interface as `sdk.Stream` (latest, received, active, error, stop)."""

    def __init__(self, sonar):
        self._s = sonar
        self.received = 0
        self.error = None
        self._active = True
        self._asked = False
        self._latest = None

    def _handle(self, msg):
        kind = msg[0]
        if kind == "ping":
            self._latest, self.received = msg[1], msg[2]
        elif kind == "end":
            self.received = msg[1]
            self._active = False
        elif kind == "error":
            from .sdk import ApiError
            self.error = ApiError(msg[1])
            self._active = False
        elif kind == "closed":
            self.received = max(self.received, msg[1])
            self._active = False

    def latest(self, timeout=None, only_new=True):
        if not only_new:
            return self._latest
        if self._latest is None and self._active:
            if not self._asked:
                try:
                    self._s._conn.send(("want",))
                except OSError:              # the child already finished (short file): read what it sent
                    pass
                self._asked = True
            end = time.time() + (timeout if timeout is not None else 1e9)
            while self._latest is None and self._active:
                left = end - time.time()
                try:
                    if left <= 0 or not self._s._conn.poll(min(left, 0.5)):
                        if left <= 0:
                            return None
                        continue
                    self._handle(self._s._conn.recv())
                except (EOFError, OSError):
                    self._active = False
        p, self._latest = self._latest, None
        if p is not None:
            self._asked = False
        if p is None and self.error:
            raise self.error
        return p

    @property
    def active(self):
        return self._active

    def stop(self):
        self._s.close()


class RemoteHead:
    def __init__(self, sonar, index, config):
        self.sonar, self.index, self.config = sonar, index, config

    @property
    def model(self):
        return self.config.model

    @property
    def limits(self):
        return self.config.range_min, self.config.range_max

    def image(self, ping, kind="xy"):
        return (_image.xy if kind == "xy" else _image.rtheta)(self.config, ping)

    def adjust_range(self, start, stop):
        self.sonar._conn.send(("range", float(start), float(stop)))

    def stream(self, real_time=False, on_ping=None, on_demand=None):
        if on_ping is not None:
            raise ValueError("in the separate process, recording happens in the child (record/npz)")
        return self.sonar._stream

    def __repr__(self):
        lo, hi = self.limits
        return f"<Head {self.index} {self.model} {lo:.1f}–{hi:.1f} m, {len(self.config.transducers)} transducers>"


class RemoteSonar:
    """Opens `source` in a child process. Initial settings, recording and NPZ happen there."""

    def __init__(self, source, head=0, range_=None, gain=None, tvg=None, record=None, npz=None,
                 real_time=False, timeout=30.0):
        ctx = mp.get_context("spawn")
        self._conn, child = ctx.Pipe()
        settings = {"range": tuple(range_) if range_ else None, "gain": gain, "tvg": tvg}
        self._proc = ctx.Process(target=_worker, args=(child, source, head, settings, record, npz, real_time),
                                 daemon=True)
        self._proc.start()
        if not self._conn.poll(timeout):
            self.close()
            from .sdk import ApiError
            raise ApiError(f"the acquisition process did not answer within {timeout:.0f} s")
        msg = self._conn.recv()
        if msg[0] != "info":
            self.close()
            from .sdk import ApiError
            raise ApiError(msg[1] if msg[0] == "error" else f"unexpected response: {msg[0]}")
        info = msg[1]
        self.live, self.model, self.sonar_type = info["live"], info["model"], info["sonar_type"]
        self.serial, self.firmware, self.clock_offset = info["serial"], info["firmware"], info["clock_offset"]
        self.heads = [RemoteHead(self, i, c) for i, c in enumerate(info["configs"])]
        self._index = head
        self._stream = RemoteStream(self)

    def head(self, i=0):
        return self.heads[i]

    def close(self):
        """Asks the child to stop, waits for it to close the recording and ends it."""
        if self._proc.is_alive():
            try:
                self._conn.send(("stop",))
                end = time.time() + 30
                while self._proc.is_alive() and time.time() < end:
                    if self._conn.poll(0.2):
                        msg = self._conn.recv()
                        if hasattr(self, "_stream"):
                            self._stream._handle(msg)
                        if msg[0] == "closed":
                            break
            except (OSError, EOFError):
                pass
            self._proc.join(10)
            if self._proc.is_alive():
                self._proc.terminate()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
