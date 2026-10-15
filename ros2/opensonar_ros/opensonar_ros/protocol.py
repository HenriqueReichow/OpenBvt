"""Client of the opensonar service wire protocol (docs/SERVER.md), on its own: needs only pyzmq and numpy,
not the opensonar library (the ROS Python is not the one bundled in the opensonar .deb).
"""
import json
import shutil
import subprocess
import time

import numpy as np
import zmq

PROTOCOL_VERSION = 1


class ServiceError(Exception):
    pass


class Commands:
    """REQ socket: one JSON command, one JSON reply (docs/SERVER.md §2)."""

    def __init__(self, address, timeout=20.0, context=None):
        self.address, self.timeout = address, timeout
        self.ctx = context or zmq.Context.instance()
        self._open()

    def _open(self):
        self.sock = self.ctx.socket(zmq.REQ)
        self.sock.setsockopt(zmq.LINGER, 0)
        self.sock.connect(self.address)

    def call(self, cmd, timeout=None, **args):
        self.sock.send(json.dumps({"cmd": cmd, **args}).encode())
        if not self.sock.poll(int((timeout or self.timeout) * 1000)):
            self.sock.close(0)
            self._open()
            raise ServiceError(f"no reply from {self.address}")
        reply = json.loads(self.sock.recv())
        if not reply.pop("ok", False):
            raise ServiceError(reply.get("error", "unknown error"))
        return reply

    def alive(self):
        try:
            self.call("status", timeout=1.0)
            return True
        except ServiceError:
            return False

    def close(self):
        self.sock.close(0)


class Ping:
    """One decoded ping message (docs/SERVER.md §3)."""

    def __init__(self, topic, meta, signals, signals_q, images):
        self.topic, self.meta = topic, meta
        self.signals, self.signals_q = signals, signals_q
        self.images = images                     # kind → (np.uint16 image, image metadata)


def decode(parts):
    meta = json.loads(parts[1])
    arrays = [np.frombuffer(p, "<u2") for p in parts[2:]]
    n_i, n_q = len(meta["signals"]), len(meta["signals_q"])
    images = {}
    described = meta.get("images")
    if described is None:
        described = [meta["image"]] if meta.get("image") else []
    for k, im in enumerate(described):
        images[im["kind"]] = (arrays[n_i + n_q + k].reshape(im["rows"], im["cols"]), im)
    return Ping(bytes(parts[0]).decode(), meta, arrays[:n_i], arrays[n_i:n_i + n_q], images)


class Subscription:
    """SUB socket for pings (`ping/…`) and event marks (`event/…`)."""

    def __init__(self, address, prefixes=("ping/", "event/"), context=None):
        self.ctx = context or zmq.Context.instance()
        self.sock = self.ctx.socket(zmq.SUB)
        self.sock.setsockopt(zmq.LINGER, 0)
        self.sock.setsockopt(zmq.RCVHWM, 2000)
        for p in prefixes:
            self.sock.setsockopt(zmq.SUBSCRIBE, p.encode())
        self.sock.connect(address)

    def recv(self, timeout):
        """("ping", Ping) | ("event", dict) | None after `timeout` s."""
        if not self.sock.poll(int(timeout * 1000)):
            return None
        parts = self.sock.recv_multipart()
        topic = bytes(parts[0]).decode()
        if topic.startswith("event/"):
            return "event", json.loads(parts[1])
        return "ping", decode(parts)

    def close(self):
        self.sock.close(0)


def start_service(command, rep_port, pub_port, wait=20.0, log=None):
    """Starts `opensonar server` as a child process and waits until it answers. Returns the Popen."""
    args = command.split() if isinstance(command, str) else list(command)
    if shutil.which(args[0]) is None:
        raise ServiceError(f"cannot start the opensonar service: {args[0]!r} not found "
                           "(install the opensonar .deb, or set the server_command parameter)")
    proc = subprocess.Popen(args + ["--bind", "127.0.0.1", "--rep-port", str(rep_port), "--pub-port", str(pub_port)],
                            stdout=log or subprocess.DEVNULL, stderr=subprocess.STDOUT if log else subprocess.DEVNULL)
    c = Commands(f"tcp://127.0.0.1:{rep_port}")
    end = time.time() + wait
    try:
        while not c.alive():
            if proc.poll() is not None or time.time() > end:
                proc.kill()
                raise ServiceError("the opensonar service did not start")
            time.sleep(0.2)
    finally:
        c.close()
    return proc
