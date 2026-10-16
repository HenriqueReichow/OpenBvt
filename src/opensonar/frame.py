"""Common frame for every sonar family: beamformed intensities on a (range, beam angle) grid.

Families that beamform in hardware hand out frames directly; families that deliver raw signals
(P900/M900) build one from their R-Theta image. Everything downstream (XY image, 3D) only needs this.
"""
from dataclasses import dataclass

import numpy as np


@dataclass
class Frame:
    number: int
    timestamp: float              # Unix s (UTC)
    intensity: np.ndarray         # (samples, beams); row 0 = nearest range, column 0 = first angle
    ranges: np.ndarray            # m, one per row, increasing
    beam_angles: np.ndarray       # rad, one per column, increasing; 0 = straight ahead, + = starboard
    sound_speed: float            # m/s
    vertical_fov: float = None    # rad, elevation aperture of the beams; None when unknown
    raw_header: bytes = None      # the frame header as the sonar wrote it, to record the frame back

    @property
    def n_samples(self):
        return self.intensity.shape[0]

    @property
    def n_beams(self):
        return self.intensity.shape[1]

    # the fields of a raw-signal ping that the service, recording and display read
    tz_offset = 0
    signals = signals_q = ()

    @property
    def range_start(self):
        return float(self.ranges[0])

    @property
    def range_stop(self):
        return float(self.ranges[-1])

    @property
    def res(self):
        """Range step in m."""
        return float((self.ranges[-1] - self.ranges[0]) / max(len(self.ranges) - 1, 1))


def from_rtheta(ping, img, geo, sound_speed):
    """Frame from an R-Theta image (image.rtheta), whose row 0 is the far range."""
    return Frame(number=ping.number, timestamp=ping.timestamp, intensity=img[::-1],
                 ranges=np.asarray(geo["ranges"], dtype=np.float64)[::-1],
                 beam_angles=np.asarray(geo["angles"], dtype=np.float64), sound_speed=float(sound_speed))


def to_rtheta(frame):
    """(R-Theta image, geometry) in the layout of image.rtheta: row 0 = far range."""
    return frame.intensity[::-1].astype(np.uint16), dict(res=frame.res, rows=frame.n_samples, cols=frame.n_beams,
                                       ranges=frame.ranges[::-1], angles=frame.beam_angles)


def render(config, ping, kind="xy", smooth=True, crop=False):
    """(image, geometry) of a raw-signal ping (with its head configuration) or of a Frame (config unused;
    smooth and crop do not apply)."""
    if isinstance(ping, Frame):
        if kind not in ("xy", "rtheta"):
            raise ValueError(f"unknown image kind: {kind!r}")
        return to_xy(ping) if kind == "xy" else to_rtheta(ping)
    from . import image
    if kind == "xy":
        return image.xy(config, ping, smooth, crop)
    if kind == "rtheta":
        return image.rtheta(config, ping, smooth)
    raise ValueError(f"unknown image kind: {kind!r}")


def to_xy(frame, resolution=None):
    """(u16 XY image, geometry) by nearest-neighbour lookup: row 0 = maximum y, sonar origin at
    geo['origin_row'/'origin_col']; pixels outside the fan are 0. resolution in m/pixel
    (default: the range step)."""
    r, th = frame.ranges, frame.beam_angles
    res = float(resolution or frame.res)
    r_max = r[-1]
    x_lo, x_hi = r_max * min(np.sin(th[0]), 0.0), r_max * max(np.sin(th[-1]), 0.0)
    y_hi = r_max
    cols = int(np.ceil((x_hi - x_lo) / res)) + 1
    rows = int(np.ceil(y_hi / res)) + 1
    X = x_lo + res * np.arange(cols)[None, :]
    Y = y_hi - res * np.arange(rows)[:, None]
    R, T = np.hypot(X, Y), np.arctan2(X, Y)
    ri = np.rint(np.interp(R, r, np.arange(len(r)), left=-1, right=-1)).astype(int)
    ti = np.rint(np.interp(T, th, np.arange(len(th)), left=-1, right=-1)).astype(int)
    inside = (ri >= 0) & (ti >= 0)
    out = np.zeros((rows, cols), dtype=np.uint16)
    out[inside] = frame.intensity[ri[inside], ti[inside]]
    geo = dict(res=res, rows=rows, cols=cols, origin_row=int(np.rint(y_hi / res)),
               origin_col=int(np.rint(-x_lo / res)), X=X, Y=Y)
    return out, geo
