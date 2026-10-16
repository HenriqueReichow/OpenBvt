"""Command line.

opensonar export FILE.son|FILE.aris DIR/ [--format png|tiff|npz] [--type xy|rtheta] [--ping N]
opensonar live IP|FILE.son [--head N] [--range [START] STOP] [--gain G] [--tvg T] [--npz DIR] [--record FILE.son] [--pings N]
opensonar discover
"""
import argparse
import json
import os
import sys
import time

from .son import SonError, OpenSonarError


def _save(img, geo, kind, fmt, path, ping):
    from .media import save_image
    save_image(img, geo, kind, fmt, path, ping)


def export(path, out_dir, fmt="png", kind="xy", pings=None, out=print, head=0, progress=None):
    """Exports pings of a recording (.son or .aris) as 16-bit PNG/TIFF, NPY or NPZ (one file per ping), or one MP4 video.
    progress(done, total) → False cancels."""
    if fmt == "mp4":
        from .media import export as media_export
        done = media_export(path, out_dir, ["mp4"], kind, pings, head, progress=progress)
        out(f"{done} ping(s) in a video in {out_dir}")
        return done
    from .sdk import open as open_sonar
    sonar = open_sonar(path)
    h = sonar.head(head)
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(path))[0]
    ext = {"png": "png", "tiff": "tif", "npz": "npz", "npy": "npy"}[fmt]
    n = h.n_pings
    indices = list(range(n) if pings is None else pings)
    suffix = f"_h{head}" if head else ""
    done = 0
    for i in indices:
        if not 0 <= i < n:
            raise SonError(f"ping {i} out of range 0..{n - 1}")
        ping = h.ping(i)
        img, geo = h.image(ping, kind)
        target = os.path.join(out_dir, f"{base}{suffix}_{kind}_{i:05d}.{ext}")
        _save(img, geo, kind, fmt, target, ping)
        done += 1
        if progress is not None and progress(done, len(indices)) is False:
            break
    out(f"{done} {kind} image(s) in {out_dir}")
    return done


def _banner(s, out):
    if s.live:
        out(f"connected: {s.model or 'sonar'} type {s.sonar_type} serial {s.serial} firmware {s.firmware} "
            f"(sonar clock {s.clock_offset:+.3f} s)")
    else:
        out(f"file: {s.model} serial {s.serial}")
    for h in s.heads:
        out(f"  {h!r}")


def live(source, head=0, range_=None, gain=None, tvg=None, npz=None, n_pings=None, window=True,
         record=None, out=print):
    """Opens the source (sonar on the network or .son), applies the settings and shows/records the XY fan.

    With a window: acquisition, recording and NPZ in a separate process (opensonar.process); the window
    always draws the latest ping. Without a window: everything in this process, ping by ping.
    record: .son (rev 14) with every ping received; npz: one .npz per ping received.
    """
    if window:
        from .process import RemoteSonar
        from .viewer import Viewer
        s = RemoteSonar(source, head, range_, gain, tvg, record, npz)
        try:
            _banner(s, out)
            v = Viewer(s, head, png_dir=npz or ".")
            out("window: space pause · ↑/↓ range · c colours · l log/linear · g grid · s PNG · q quit")
            v.run(n_pings)
        except KeyboardInterrupt:
            out("interrupted")
        finally:
            s.close()
        n = s._stream.received
        if record:
            out(f"{n} ping(s) recorded in {record}")
        return n

    from .sdk import open as open_sonar
    from .writer import raw_writer
    with open_sonar(source) as s:
        _banner(s, out)
        h = s.head(head)
        if range_:
            start, stop = (h.limits[0], range_[0]) if len(range_) == 1 else range_
            h.adjust_range(start, stop)
            out(f"requested range: {max(start, h.limits[0]):.2f}–{min(stop, h.limits[1]):.2f} m")
        if gain is not None:
            h.adjust_gain(gain)
        if tvg is not None:
            h.adjust_tvg(tvg)
        if npz:
            os.makedirs(npz, exist_ok=True)
        writer = raw_writer(record, s)[0] if record else None
        t0, k = time.time(), 0
        try:
            for p in h.pings(n=n_pings):
                if writer:
                    writer.add(p, head)
                if npz:
                    img, geo = h.image(p, "xy")
                    _save(img, geo, "xy", "npz", os.path.join(npz, f"ping_{k:05d}.npz"), p)
                k += 1
                out(f"ping {p.number}: {p.range_start:.2f}–{p.range_stop:.2f} m, "
                    f"{p.signals[0].size if p.signals else p.n_samples} samples, "
                    f"{k / (time.time() - t0):.1f} pings/s")
        except KeyboardInterrupt:
            out("interrupted")
        finally:
            if writer:
                writer.close()
                out(f"{k} ping(s) recorded in {record}")
    return k


CTL_USAGE = """commands:
  status | heads | discover [TIMEOUT] | connect [IP|FILE.son] | disconnect
  range HEAD [START] STOP | gain HEAD G | tvg HEAD T
  start [HEAD ...] [image] | stop | record FILE.son | stop_record | mark [TEXT ...]
  seek PING | step [DELTA] (files) | quit"""


def ctl(address, words, out=print):
    """`opensonar ctl`: one command to the service; prints the JSON reply."""
    from .client import Client
    if not words:
        raise ValueError("no command\n" + CTL_USAGE)
    cmd, a = words[0], words[1:]
    num = [float(x) for x in a if x != "image"] if cmd in ("range", "gain", "tvg", "start") else []
    if cmd == "range" and len(num) in (2, 3):
        args = {"head": int(num[0]), "start": num[1] if len(num) == 3 else None, "stop": num[-1]}
    elif cmd in ("gain", "tvg") and len(num) == 2:
        args = {"head": int(num[0]), "value": num[1]}
    elif cmd == "start":
        args = {"heads": [int(x) for x in num] or [0], "image": "image" in a}
    elif cmd == "record" and len(a) == 1:
        args = {"path": os.path.abspath(a[0])}
    elif cmd == "connect" and len(a) <= 1:
        args = {"source": (a[0] if not a or not os.path.exists(a[0]) else os.path.abspath(a[0])) if a else None}
    elif cmd == "discover" and len(a) <= 1:
        args = {"timeout": float(a[0])} if a else {}
    elif cmd == "mark":
        args = {"text": " ".join(a)}
    elif cmd == "seek" and len(a) == 1:
        args = {"index": int(a[0])}
    elif cmd == "step" and len(a) <= 1:
        args = {"delta": int(a[0]) if a else 1}
    elif cmd in ("status", "heads", "disconnect", "stop", "stop_record", "quit") and not a:
        args = {}
    else:
        raise ValueError(f"bad command: {' '.join(words)}\n" + CTL_USAGE)
    with Client(address) as c:
        reply = c.call(cmd, **args)
    out(json.dumps(reply, indent=2, default=str))
    return reply


def main(argv=None):
    ap = argparse.ArgumentParser(prog="opensonar", description="Open SDK for P900 and M900 sonars.")
    sub = ap.add_subparsers(dest="command", required=True)
    ex = sub.add_parser("export", help="export images from a .son or .aris file")
    ex.add_argument("file", help=".son or .aris file")
    ex.add_argument("out_dir", help="output directory")
    ex.add_argument("--format", choices=("png", "tiff", "npy", "npz", "mp4"), action="append",
                    help="png/tiff: lossless 16 bit; npy: uint16 array; npz: uint16 array + metadata; "
                         "mp4: colour video (Copper, auto intensity); repeatable (default: png)")
    ex.add_argument("--type", choices=("xy", "rtheta"), default="xy", help="Cartesian fan or R-Theta (default: xy)")
    ex.add_argument("--ping", type=int, action="append",
                    help="ping index (0 = first); can be repeated; default: all")
    lv = sub.add_parser("live", help="live XY fan through the native driver, or playback of a .son")
    lv.add_argument("source", help="sonar IP (or ip:port), or a .son file")
    lv.add_argument("--head", type=int, default=0, help="head (default: 0)")
    lv.add_argument("--range", type=float, nargs="+", metavar="M",
                    help="stop range, or start and stop, in metres")
    lv.add_argument("--gain", type=float, help="gain")
    lv.add_argument("--tvg", type=float, help="TVG")
    lv.add_argument("--npz", metavar="DIR", help="write one .npz per ping (XY image + metadata)")
    lv.add_argument("--record", metavar="FILE.son", help="record every ping in a .son (rev 14)")
    lv.add_argument("--pings", type=int, help="stop after N pings (default: until closed/Ctrl+C)")
    lv.add_argument("--no-window", action="store_true", help="no window (terminal/NPZ only)")
    ds = sub.add_parser("discover", help="look for sonars on the network (multicast 224.192.45.34)")
    ds.add_argument("--timeout", type=float, default=2.0, help="seconds to wait (default: 2)")
    sv = sub.add_parser("server", help="service: owns the sonar, records, publishes pings over ZeroMQ (docs/SERVER.md)")
    sv.add_argument("source", nargs="?", help="connect right away to this IP (ip:port) or .son; default: wait for `connect`")
    sv.add_argument("--bind", default="127.0.0.1", help="interface to listen on (default: 127.0.0.1; 0.0.0.0 = all)")
    sv.add_argument("--rep-port", type=int, default=4545, help="command port (default: 4545)")
    sv.add_argument("--pub-port", type=int, default=4546, help="ping port (default: 4546)")
    sv.add_argument("--head", type=int, action="append", help="head to ping with SOURCE (repeatable; default: 0)")
    sv.add_argument("--image", action="store_true", help="also publish the XY image (u16) of every ping")
    sv.add_argument("--record", metavar="FILE.son", help="record every ping from the start (with SOURCE)")
    sub.add_parser("gui", help="graphical interface (starts the service by itself if needed)")
    sub.add_parser("network", help="activate the opensonar-net network profile (192.168.1.10/24 on the wired card)")
    ct = sub.add_parser("ctl", help="send one command to the service", epilog=CTL_USAGE,
                        formatter_class=argparse.RawDescriptionHelpFormatter)
    ct.add_argument("--address", default="tcp://127.0.0.1:4545", help="service command address")
    ct.add_argument("words", nargs=argparse.REMAINDER, help="command and arguments")
    args = ap.parse_args(argv)
    try:
        if args.command == "network":
            from . import netsetup
            try:
                print(netsetup.setup())
            except netsetup.NetSetupError as e:
                print(f"error: {e}", file=sys.stderr)
                return 1
            return 0
        if args.command == "gui":
            from .gui import main as gui_main
            return gui_main()
        if args.command == "server":
            from .server import run
            run(args.source, args.bind, args.rep_port, args.pub_port, args.head, args.image, args.record)
        elif args.command == "ctl":
            ctl(args.address, args.words)
        elif args.command == "export":
            for fmt in dict.fromkeys(args.format or ["png"]):
                export(args.file, args.out_dir, fmt, args.type, args.ping)
        elif args.command == "discover":
            from .net import discover
            found = discover(args.timeout)
            for a in found:
                print(f"{a.get('Host')}:{a.get('Port')}  UUID={a.get('UUID', '')}  "
                      f"multicast={a.get('MulticastIP', '')}:{a.get('MulticastPort', '')}")
            if not found:
                print("no sonar answered")
                return 1
        else:
            if args.range and len(args.range) > 2:
                ap.error("--range takes 1 or 2 values")
            live(args.source, args.head, args.range, args.gain, args.tvg, args.npz, args.pings,
                 not args.no_window, args.record)
    except (OpenSonarError, OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
