"""Output formats besides .son: per-ping images (NPY, NPZ, PNG, TIFF) with an index CSV, and MP4 video.
No Qt here: used by the service (recording), the GUI (video) and `opensonar export`.

Per-ping layout, for a base path `/data/session` and format `npy`:
    /data/session_npy/h0/ping_000000.npy …      XY (or R-Theta) image, uint16
    /data/session_npy/h0/index.csv              one row per ping: file, number, utc, timestamp, ranges,
                                                resolution and origin (pixel of the sonar)
"""
import csv
import os
from datetime import datetime, timezone

import numpy as np

IMAGE_FORMATS = ("npy", "npz", "png", "tiff")
ALL_FORMATS = ("son",) + IMAGE_FORMATS + ("mp4",)
EXT = {"npy": "npy", "npz": "npz", "png": "png", "tiff": "tif"}
INDEX_FIELDS = ("file", "number", "utc", "timestamp", "tz_offset", "range_start", "range_stop", "kind",
                "rows", "cols", "res", "origin_row", "origin_col")


def utc_iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def save_image(img, geo, kind, fmt, path, ping):
    """One ping image in one format (16 bit, lossless). NPZ also carries the metadata."""
    if fmt == "npy":
        np.save(path, img.astype(np.uint16))
    elif fmt in ("png", "tiff"):
        from PIL import Image
        Image.fromarray(img.astype(np.uint16)).save(path)
    elif fmt == "npz":
        meta = dict(kind=kind, ping=ping.number, timestamp=ping.timestamp, tz_offset_s=ping.tz_offset,
                    range_start=ping.range_start, range_stop=ping.range_stop, resolution_m=float(geo["res"]))
        if kind == "rtheta":
            meta.update(range_m=geo["ranges"], angle_deg=np.rad2deg(geo["angles"]))
        else:
            meta.update(origin_row=geo["origin_row"], origin_col=geo["origin_col"])
        np.savez_compressed(path, image=img, **meta)
    else:
        raise ValueError(f"unknown image format {fmt!r}")


class ImageSink:
    """Writes every ping image in the chosen formats, one folder per format and head, with index.csv."""

    def __init__(self, base, formats, kind="xy"):
        self.base, self.kind = base, kind
        self.formats = [f for f in formats if f in IMAGE_FORMATS]
        self.count = {}                                   # head → pings written
        self._index = {}                                  # (fmt, head) → (file, csv writer)

    def folder(self, fmt, head):
        return f"{self.base}_{fmt}{os.sep}h{head}"

    def _writer(self, fmt, head):
        key = (fmt, head)
        if key not in self._index:
            d = self.folder(fmt, head)
            os.makedirs(d, exist_ok=True)
            f = open(os.path.join(d, "index.csv"), "w", newline="")
            w = csv.writer(f)
            w.writerow(INDEX_FIELDS)
            self._index[key] = (f, w)
        return self._index[key][1]

    def write(self, head, ping, img, geo):
        k = self.count.get(head, 0)
        for fmt in self.formats:
            index = self._writer(fmt, head)                       # creates the folder and index.csv
            name = f"ping_{k:06d}.{EXT[fmt]}"
            save_image(img, geo, self.kind, fmt, os.path.join(self.folder(fmt, head), name), ping)
            index.writerow([name, ping.number, utc_iso(ping.timestamp), f"{ping.timestamp:.6f}", ping.tz_offset,
                            f"{ping.range_start:.4f}", f"{ping.range_stop:.4f}", self.kind, img.shape[0],
                            img.shape[1], f"{float(geo['res']):.6g}", geo.get("origin_row", ""),
                            geo.get("origin_col", "")])
        self.count[head] = k + 1

    def close(self):
        for f, _ in self._index.values():
            f.close()
        self._index = {}


class VideoWriter:
    """MP4 (H.264) from RGB frames. The size is fixed by the first frame; later frames are scaled to it."""

    def __init__(self, path, fps=10.0):
        self.path, self.fps = path, float(fps)
        self.size = None
        self.frames = 0
        self._gen = None

    def add(self, rgb):
        rgb = np.ascontiguousarray(rgb[..., :3], np.uint8)
        if self._gen is None:
            import imageio_ffmpeg
            h, w = rgb.shape[:2]
            self.size = (w - w % 2, h - h % 2)                # H.264 needs even sizes
            self._gen = imageio_ffmpeg.write_frames(self.path, self.size, fps=self.fps, codec="libx264",
                                                    pix_fmt_in="rgb24", macro_block_size=1, quality=8)
            self._gen.send(None)
        w, h = self.size
        if rgb.shape[:2] != (h, w):
            if rgb.shape[0] - h in (0, 1) and rgb.shape[1] - w in (0, 1):
                rgb = rgb[:h, :w]                                 # the first frame's odd row/column
            else:
                from PIL import Image
                rgb = np.asarray(Image.fromarray(rgb).resize(self.size, Image.BILINEAR))
        self._gen.send(np.ascontiguousarray(rgb).tobytes())
        self.frames += 1

    def close(self):
        if self._gen is not None:
            self._gen.close()
            self._gen = None


def fan_rgb(img, mapper, background=(0, 0, 0)):
    """Colour-mapped fan (display.ColorMapper) on a solid background → RGB uint8."""
    rgba = mapper.map(img).astype(np.float32)
    a = rgba[..., 3:4] / 255.0
    bg = np.array(background, np.float32)
    return (rgba[..., :3] * a + bg * (1 - a)).astype(np.uint8)


def export(path, out_dir, formats=("png",), kind="xy", pings=None, head=0, mapper=None, fps=None,
           progress=None):
    """Exports pings of a .son in several formats at once.

    formats: any of npy, npz, png, tiff (one file per ping + index.csv, in out_dir/<name>_<fmt>/h<head>/)
    and mp4 (out_dir/<name>_h<head>_<kind>.mp4, coloured with `mapper`, default Copper + auto intensity;
    fps None = the recorded ping rate). progress(done, total) → False cancels. Returns the number of pings.
    """
    from . import display
    from .image import rtheta, xy
    from .son import SonError, SonFile
    son = SonFile(path)
    config = son.heads[head]
    n = son.n_pings(head)
    indices = list(range(n) if pings is None else pings)
    for i in indices:
        if not 0 <= i < n:
            raise SonError(f"ping {i} out of range 0..{n - 1}")
    unknown = set(formats) - set(IMAGE_FORMATS) - {"mp4"}
    if unknown:
        raise ValueError(f"unknown format(s): {', '.join(sorted(unknown))}")
    os.makedirs(out_dir, exist_ok=True)
    name = os.path.splitext(os.path.basename(path))[0]
    sink = ImageSink(os.path.join(out_dir, name), formats, kind)
    video = None
    if "mp4" in formats:
        if fps is None:
            t = [son.ping(i, head).timestamp for i in indices[:50]]
            dt = np.median(np.diff(t)) if len(t) > 1 else 0.1
            fps = min(max(1.0 / dt if dt > 0 else 10.0, 1.0), 60.0)
        video = VideoWriter(os.path.join(out_dir, f"{name}_h{head}_{kind}.mp4"), fps)
        mapper = mapper or display.ColorMapper(display.colormap("Copper"))
    render = (lambda p: xy(config, p, crop=True)) if kind == "xy" else (lambda p: rtheta(config, p))
    done = 0
    try:
        for i in indices:
            p = son.ping(i, head)
            img, geo = render(p)
            sink.write(head, p, img, geo)
            if video is not None:
                video.add(fan_rgb(img, mapper))
            done += 1
            if progress is not None and progress(done, len(indices)) is False:
                break
    finally:
        sink.close()
        if video is not None:
            video.close()
    return done
