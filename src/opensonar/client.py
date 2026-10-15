"""Client of the opensonar service (`opensonar server`): commands (REQ) and pings (SUB). Wire format in docs/SERVER.md.

    from opensonar.client import Client, Subscriber
    c = Client()                                   # tcp://127.0.0.1:4545
    c.connect("192.168.1.45"); c.range(0, 1, 20); c.start([0])
    with Subscriber(head=0) as sub:                # tcp://127.0.0.1:4546
        for msg in sub:
            print(msg.meta["utc"], msg.ping.number, msg.image is not None)
"""
import json
from dataclasses import dataclass

import numpy as np

from .server import PUB_PORT, REP_PORT
from .son import OpenSonarError, Ping


class ServerError(OpenSonarError):
    """The service answered a command with an error."""


class Client:
    def __init__(self, address=f"tcp://127.0.0.1:{REP_PORT}", timeout=20.0, context=None):
        import zmq
        self.zmq, self.address, self.timeout = zmq, address, timeout
        self.ctx = context or zmq.Context.instance()
        self._open()

    def _open(self):
        self.sock = self.ctx.socket(self.zmq.REQ)
        self.sock.setsockopt(self.zmq.LINGER, 0)
        self.sock.connect(self.address)

    def call(self, cmd, **args):
        """Sends one command and returns the reply (dict). Raises ServerError if the service reports an error."""
        self.sock.send(json.dumps({"cmd": cmd, **args}).encode())
        if not self.sock.poll(int(self.timeout * 1000)):
            self.sock.close(0)                     # a REQ socket without a reply cannot be reused
            self._open()
            raise ServerError(f"no reply from {self.address} in {self.timeout:g} s")
        reply = json.loads(self.sock.recv())
        if not reply.pop("ok", False):
            raise ServerError(reply.get("error", "unknown error"))
        return reply

    # one method per command
    def discover(self, timeout=2.0, probe=True):
        return self.call("discover", timeout=timeout, probe=probe)["sonars"]

    def connect(self, source=None, **options):
        return self.call("connect", source=source, **options)

    def disconnect(self):
        return self.call("disconnect")

    def heads(self):
        return self.call("heads")

    def range(self, head, start, stop):
        return self.call("range", head=head, start=start, stop=stop)

    def gain(self, head, value):
        return self.call("gain", head=head, value=value)

    def tvg(self, head, value):
        return self.call("tvg", head=head, value=value)

    def start(self, heads=(0,), image=False, smooth=True, speed=None):
        """image: False, True (= XY) or a list of "xy"/"rtheta"."""
        return self.call("start", heads=list(heads), image=image, smooth=smooth, speed=speed)

    def image(self, enabled=True, smooth=True, kinds=None):
        return self.call("image", enabled=enabled, smooth=smooth, kinds=kinds)

    def config(self, head=None):
        return self.call("config", head=head)["heads"]

    def stop(self):
        return self.call("stop")

    def record(self, path, formats=("son",)):
        return self.call("record", path=str(path), formats=list(formats))

    def stop_record(self):
        return self.call("stop_record")

    def seek(self, index):
        return self.call("seek", index=int(index))

    def step(self, delta=1, image=None, smooth=None):
        return self.call("step", delta=int(delta), image=image, smooth=smooth)

    def mark(self, text=""):
        return self.call("mark", text=str(text))

    def status(self):
        return self.call("status")

    def quit(self):
        return self.call("quit")

    def close(self):
        self.sock.close(0)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


@dataclass
class PingMessage:
    topic: str
    meta: dict             # metadata JSON (docs/SERVER.md §3)
    ping: Ping             # the same object the SDK uses (signals as np.uint16)
    image: object = None   # XY image (np.uint16, rows × cols) or None
    images: dict = None    # kind ("xy", "rtheta") → (np.uint16 image, its metadata)


def decode(parts):
    """Multipart ping message → PingMessage."""
    meta = json.loads(parts[1])
    arrays = [np.frombuffer(p, meta.get("dtype", "<u2")).astype(np.uint16) for p in parts[2:]]
    n_i, n_q = len(meta["signals"]), len(meta["signals_q"])
    if len(arrays) < n_i + n_q:
        raise ServerError(f"ping message with {len(arrays)} data part(s), expected {n_i + n_q}")
    ping = Ping(meta["number"], meta["timestamp"], meta["tz_offset"], meta["range_start"], meta["range_stop"],
                arrays[:n_i], arrays[n_i:n_i + n_q])
    images = {}
    described = meta.get("images")
    if described is None:                                   # older services: at most the XY image
        described = [meta["image"]] if meta.get("image") else []
    for k, im in enumerate(described):
        images[im["kind"]] = (arrays[n_i + n_q + k].reshape(im["rows"], im["cols"]), im)
    image = images["xy"][0] if "xy" in images else None
    return PingMessage(bytes(parts[0]).decode(), meta, ping, image, images)


class Subscriber:
    """Receives the pings of the service. serial/head select the topic (None = all)."""

    def __init__(self, address=f"tcp://127.0.0.1:{PUB_PORT}", serial=None, head=None, context=None):
        import zmq
        self.zmq = zmq
        self.ctx = context or zmq.Context.instance()
        self.sock = self.ctx.socket(zmq.SUB)
        self.sock.setsockopt(zmq.LINGER, 0)
        prefix = "ping/" if serial is None else f"ping/{serial}/"
        self.sock.setsockopt(zmq.SUBSCRIBE, prefix.encode())
        self._head = None if head is None else str(head)      # the topic prefix cannot tell 1 from 10
        self.sock.connect(address)

    def recv(self, timeout=None):
        """Next ping (PingMessage), or None after `timeout` seconds."""
        while True:
            if timeout is not None and not self.sock.poll(int(timeout * 1000)):
                return None
            parts = self.sock.recv_multipart()
            if self._head is None or bytes(parts[0]).decode().rsplit("/", 1)[-1] == self._head:
                return decode(parts)

    def __iter__(self):
        while True:
            yield self.recv()

    def close(self):
        self.sock.close(0)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class EventSubscriber:
    """Receives the event marks of the service (topic `event/<serial>`): dicts with utc, timestamp, text…"""

    def __init__(self, address=f"tcp://127.0.0.1:{PUB_PORT}", serial=None, context=None):
        import zmq
        self.ctx = context or zmq.Context.instance()
        self.sock = self.ctx.socket(zmq.SUB)
        self.sock.setsockopt(zmq.LINGER, 0)
        self.sock.setsockopt(zmq.SUBSCRIBE, ("event/" if serial is None else f"event/{serial}").encode())
        self.sock.connect(address)

    def recv(self, timeout=None):
        if timeout is not None and not self.sock.poll(int(timeout * 1000)):
            return None
        return json.loads(self.sock.recv_multipart()[1])

    def close(self):
        self.sock.close(0)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
