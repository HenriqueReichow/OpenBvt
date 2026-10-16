""".ddf recordings (docs/DDF_FORMAT.md): a file header, then frames of a frame header followed by
samples × beams u8 intensities. Like .aris, the beams are formed by an acoustic lens.

Version 3 (512-byte file header, 256-byte frame headers) is checked against real recordings. Other
versions are read when the file size matches one of the known header layouts, and marked as such.
"""
import calendar
import os
import struct

import numpy as np

from ..frame import Frame
from ..son import OpenSonarError

# file header, the same first fields in every version: offset → (name, format)
FILE_FIELDS = {
    0: ("signature", "4s"), 4: ("n_frames", "i"), 8: ("frame_rate", "i"), 12: ("high_res", "i"),
    16: ("n_beams", "i"), 20: ("sample_rate", "f"), 24: ("samples", "i"), 28: ("receiver_gain", "i"),
    32: ("window_start", "i"), 36: ("window_length", "i"), 40: ("reverse", "i"), 44: ("serial", "i"),
}
# frame header (version 3)
FRAME_FIELDS = {
    0: ("index", "i"), 4: ("frame_time", "i"), 12: ("signature", "4s"),
    20: ("year", "i"), 24: ("month", "i"), 28: ("day", "i"), 32: ("hour", "i"), 36: ("minute", "i"),
    40: ("second", "i"), 44: ("hsecond", "i"), 48: ("transmit", "i"), 52: ("window_start", "i"),
    56: ("window_length", "i"), 68: ("receiver_gain", "i"), 192: ("config_flags", "i"),
}
# (file header, frame header) sizes per version; the first one is the checked layout
LAYOUTS = {3: [(512, 256)], 4: [(1024, 1024), (512, 1024)]}
CHECKED_VERSIONS = (3,)

NOMINAL_SOUND_SPEED = 1457.0          # m/s: these files do not record the sound speed
VERTICAL_FOV = np.deg2rad(14.0)       # elevation aperture of the beams
HALF_FOV = 14.4                       # degrees

# lens distortion: beam number (0-based, 0 = the left-most) as a cubic of the angle in degrees (positive
# to starboard), per beam count. In the file, beam 0 is the right-most one.
LENS = {48: (1.0, (0.0015, -0.0036, 1.3351, 24.0976)), 96: (1.012, (0.0030, -0.0055, 2.6829, 48.04))}


class DdfError(OpenSonarError):
    pass


def _read(fields, data, offset=0):
    return {name: struct.unpack_from("<" + fmt, data, offset + off)[0] for off, (name, fmt) in fields.items()}


def delay_period(serial, high_res):
    """s per window-start step: depends on the unit (serial ≤ 18 are older) and on the resolution."""
    if serial <= 18:
        return 0.000512 if high_res else 0.001024
    return 0.000572 if high_res else 0.001144


def beam_angles(n_beams):
    """Beam centre angles (rad, increasing: left-most beam first) through the lens distortion model."""
    if n_beams not in LENS:
        raise DdfError(f"no lens model for {n_beams} beams")
    factor, (a, b, c, d) = LENS[n_beams]
    theta = np.linspace(-HALF_FOV - 2, HALF_FOV + 2, 20001)
    number = factor * (a * theta ** 3 + b * theta ** 2 + c * theta + d)     # 0-based, increasing with theta
    centres = np.interp(np.arange(n_beams), number, theta)
    return np.deg2rad(centres)


class DdfFile:
    """Read-only .ddf recording; frames are read on demand."""

    def __init__(self, path, sound_speed=NOMINAL_SOUND_SPEED):
        self.path, self.sound_speed = path, float(sound_speed)
        self._f = open(path, "rb")
        head = self._f.read(1024)
        if len(head) < 64 or head[:3] != b"DDF":
            self._f.close()
            raise DdfError(f"{path}: not a .ddf file")
        self.version = head[3]
        h = _read(FILE_FIELDS, head)
        self.n_beams, self.n_samples = h["n_beams"], h["samples"]
        self.serial, self.high_res, self.sample_rate = h["serial"], h["high_res"], h["sample_rate"]
        self.reverse = h["reverse"]
        data = self.n_beams * self.n_samples
        size = os.path.getsize(path)
        for file_header, frame_header in LAYOUTS.get(self.version, []):
            if (size - file_header) % (frame_header + data) == 0:
                break
        else:
            self._f.close()
            raise DdfError(f"{path}: version {self.version} with a size that matches no known layout")
        self.file_header, self.frame_header = file_header, frame_header
        self.checked = self.version in CHECKED_VERSIONS
        self.frame_size = frame_header + data
        self.n_frames = (size - file_header) // self.frame_size
        self.model = f"{'high' if self.high_res else 'low'} frequency, {self.n_beams} beams"
        self.beam_angles = beam_angles(self.n_beams)
        self.first = self.header(0) if self.n_frames else None

    def header(self, i):
        self._seek(i)
        return _read(FRAME_FIELDS, self._f.read(self.frame_header))

    def frame(self, i):
        """Frame i as an opensonar.frame.Frame."""
        self._seek(i)
        raw = self._f.read(self.frame_size)
        if len(raw) < self.frame_size:
            raise DdfError(f"frame {i} is truncated")
        h = _read(FRAME_FIELDS, raw)
        data = np.frombuffer(raw, np.uint8, offset=self.frame_header).reshape(self.n_samples, self.n_beams)
        c = self.sound_speed
        start = h["window_start"] * delay_period(self.serial, self.high_res) * c / 2
        ranges = start + (np.arange(self.n_samples) + 0.5) * c / (2 * self.sample_rate)
        return Frame(number=h["index"], timestamp=self.timestamp(h), intensity=data[:, ::-1].copy(),
                     ranges=ranges, beam_angles=self.beam_angles, sound_speed=c, vertical_fov=VERTICAL_FOV)

    @staticmethod
    def timestamp(h):
        """UTC: the whole seconds of frame_time (Unix), the hundredths from the local time fields, whose
        offset to UTC is the difference rounded to a quarter of an hour."""
        try:
            local = calendar.timegm((h["year"], h["month"], h["day"], h["hour"], h["minute"], h["second"]))
        except (ValueError, OverflowError):
            return float(h["frame_time"])
        if not h["frame_time"]:
            return local + h["hsecond"] / 100
        zone = round((h["frame_time"] - local) / 900) * 900
        return local + zone + h["hsecond"] / 100

    def _seek(self, i):
        if not 0 <= i < self.n_frames:
            raise DdfError(f"frame {i} out of range 0..{self.n_frames - 1}")
        self._f.seek(self.file_header + i * self.frame_size)

    def close(self):
        self._f.close()
