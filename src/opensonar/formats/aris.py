""".aris recordings: a 1024-byte file header, then frames of a 1024-byte frame header followed by
samples × beams u8 intensities (0–255 ↔ 0–80 dB). The beams are formed by an acoustic lens, so the
data is already an image on a range × beam angle grid.
"""
import os
import struct

import numpy as np

from ..frame import Frame
from ..son import OpenSonarError

SIGNATURE = 0x05464444
HEADER_SIZE = 1024

# frame header: offset → (name, struct format)
FRAME_FIELDS = {
    0: ("index", "I"), 4: ("frame_time", "Q"), 12: ("version", "I"), 20: ("pc_time", "Q"),
    436: ("ping_mode", "I"), 440: ("high_freq", "I"), 452: ("sample_period", "I"),
    464: ("sound_speed", "f"), 468: ("samples_per_beam", "I"), 476: ("sample_start_delay", "I"),
    480: ("large_lens", "I"), 484: ("system_type", "I"), 488: ("serial", "I"), 516: ("reordered", "I"),
}
SYSTEM_TYPES = {0: "1800", 1: "3000", 2: "1200"}


class ArisError(OpenSonarError):
    pass


def beams_from_ping_mode(mode):
    for n, modes in ((48, (1, 2)), (96, (3, 4, 5)), (64, (6, 7, 8)), (128, (9, 10, 11, 12))):
        if mode in modes:
            return n
    raise ArisError(f"unknown ping mode {mode}")


# beam centre angles in degrees, beam 0 first; measured lens patterns (beams are not evenly spaced).
# Positive = towards beam 0, which is the right-most (starboard) beam.
BEAM_ANGLES = {
    "standard_48": (
        13.5735, 13.1419, 12.7003, 12.2442, 11.7721, 11.2881, 10.7921, 10.2824,
        9.7597, 9.2206, 8.6630, 8.0899, 7.5026, 6.9019, 6.2897, 5.6661,
        5.0291, 4.3823, 3.7264, 3.0632, 2.3931, 1.7170, 1.0343, 0.3461,
        -0.3461, -1.0343, -1.7170, -2.3931, -3.0632, -3.7264, -4.3823, -5.0291,
        -5.6661, -6.2897, -6.9019, -7.5026, -8.0899, -8.6630, -9.2206, -9.7597,
        -10.2824, -10.7921, -11.2881, -11.7721, -12.2442, -12.7003, -13.1419, -13.5735,
    ),
    "standard_96": (
        13.7490, 13.5347, 13.3174, 13.0975, 12.8758, 12.6498, 12.4197, 12.1855,
        11.9476, 11.7069, 11.4636, 11.2173, 10.9676, 10.7144, 10.4579, 10.1985,
        9.9352, 9.6680, 9.3961, 9.1195, 8.8385, 8.5535, 8.2654, 7.9738,
        7.6781, 7.3793, 7.0774, 6.7727, 6.4652, 6.1549, 5.8416, 5.5246,
        5.2046, 4.8824, 4.5578, 4.2310, 3.9019, 3.5711, 3.2387, 2.9044,
        2.5686, 2.2314, 1.8925, 1.5519, 1.2098, 0.8664, 0.5216, 0.1757,
        -0.1706, -0.5169, -0.8628, -1.2076, -1.5510, -1.8931, -2.2337, -2.5726,
        -2.9098, -3.2456, -3.5799, -3.9123, -4.2431, -4.5722, -4.8990, -5.2236,
        -5.5458, -5.8658, -6.1828, -6.4961, -6.8064, -7.1139, -7.4186, -7.7205,
        -8.0193, -8.3150, -8.6066, -8.8947, -9.1797, -9.4607, -9.7373, -10.0092,
        -10.2764, -10.5397, -10.7991, -11.0556, -11.3088, -11.5585, -11.8048, -12.0481,
        -12.2888, -12.5267, -12.7609, -12.9910, -13.2170, -13.4387, -13.6586, -13.8759,
    ),
    "high_64": (
        14.8872, 14.4266, 13.9637, 13.4990, 13.0332, 12.5665, 12.0986, 11.6295,
        11.1593, 10.6883, 10.2165, 9.7439, 9.2706, 8.7969, 8.3228, 7.8486,
        7.3742, 6.8997, 6.4250, 5.9502, 5.4753, 5.0003, 4.5251, 4.0499,
        3.5745, 3.0991, 2.6234, 2.1473, 1.6708, 1.1939, 0.7166, 0.2389,
        -0.2389, -0.7166, -1.1939, -1.6708, -2.1473, -2.6234, -3.0991, -3.5745,
        -4.0499, -4.5251, -5.0003, -5.4753, -5.9502, -6.4250, -6.8997, -7.3742,
        -7.8486, -8.3228, -8.7969, -9.2706, -9.7439, -10.2165, -10.6883, -11.1593,
        -11.6295, -12.0986, -12.5665, -13.0332, -13.4990, -13.9637, -14.4266, -14.8872,
    ),
    "high_128": (
        15.0068, 14.7768, 14.5462, 14.3150, 14.0833, 13.8511, 13.6186, 13.3858,
        13.1528, 12.9196, 12.6861, 12.4523, 12.2182, 11.9838, 11.7491, 11.5141,
        11.2789, 11.0435, 10.8079, 10.5721, 10.3361, 10.0999, 9.8635, 9.6269,
        9.3902, 9.1534, 8.9165, 8.6795, 8.4424, 8.2053, 7.9682, 7.7310,
        7.4938, 7.2566, 7.0193, 6.7820, 6.5446, 6.3072, 6.0698, 5.8324,
        5.5949, 5.3574, 5.1199, 4.8823, 4.6447, 4.4071, 4.1695, 3.9318,
        3.6941, 3.4564, 3.2187, 2.9809, 2.7430, 2.5050, 2.2669, 2.0287,
        1.7904, 1.5520, 1.3135, 1.0749, 0.8362, 0.5974, 0.3585, 0.1196,
        -0.1194, -0.3584, -0.5973, -0.8362, -1.0750, -1.3137, -1.5523, -1.7908,
        -2.0292, -2.2675, -2.5057, -2.7438, -2.9818, -3.2197, -3.4575, -3.6952,
        -3.9329, -4.1706, -4.4083, -4.6459, -4.8835, -5.1211, -5.3587, -5.5962,
        -5.8337, -6.0712, -6.3086, -6.5460, -6.7834, -7.0208, -7.2581, -7.4954,
        -7.7326, -7.9698, -8.2070, -8.4441, -8.6812, -8.9183, -9.1553, -9.3922,
        -9.6290, -9.8657, -10.1023, -10.3387, -10.5749, -10.8109, -11.0467, -11.2823,
        -11.5177, -11.7529, -11.9879, -12.2226, -12.4570, -12.6911, -12.9249, -13.1584,
        -13.3916, -13.6246, -13.8574, -14.0899, -14.3221, -14.5538, -14.7850, -15.0156,
    ),
    "telephoto_48": (
        7.3450, 7.0480, 6.7490, 6.4470, 6.1440, 5.8390, 5.5330, 5.2250,
        4.9150, 4.6040, 4.2910, 3.9760, 3.6600, 3.3430, 3.0270, 2.7090,
        2.3920, 2.0750, 1.7570, 1.4390, 1.1200, 0.8000, 0.4800, 0.1600,
        -0.1600, -0.4800, -0.8000, -1.1200, -1.4390, -1.7570, -2.0750, -2.3920,
        -2.7090, -3.0270, -3.3430, -3.6600, -3.9760, -4.2910, -4.6040, -4.9150,
        -5.2250, -5.5330, -5.8390, -6.1440, -6.4480, -6.7490, -7.0490, -7.3460,
    ),
    "telephoto_96": (
        7.4190, 7.2710, 7.1230, 6.9740, 6.8240, 6.6740, 6.5230, 6.3720,
        6.2200, 6.0680, 5.9150, 5.7630, 5.6090, 5.4560, 5.3020, 5.1480,
        4.9930, 4.8380, 4.6820, 4.5260, 4.3700, 4.2130, 4.0550, 3.8970,
        3.7390, 3.5810, 3.4220, 3.2640, 3.1050, 2.9470, 2.7880, 2.6300,
        2.4710, 2.3130, 2.1540, 1.9950, 1.8360, 1.6770, 1.5180, 1.3590,
        1.1990, 1.0390, 0.8800, 0.7200, 0.5600, 0.4000, 0.2400, 0.0800,
        -0.0800, -0.2400, -0.4000, -0.5600, -0.7200, -0.8800, -1.0390, -1.1990,
        -1.3590, -1.5180, -1.6770, -1.8360, -1.9950, -2.1540, -2.3130, -2.4710,
        -2.6300, -2.7880, -2.9470, -3.1050, -3.2640, -3.4220, -3.5810, -3.7390,
        -3.8970, -4.0550, -4.2130, -4.3700, -4.5260, -4.6820, -4.8380, -4.9930,
        -5.1480, -5.3020, -5.4560, -5.6090, -5.7630, -5.9150, -6.0680, -6.2200,
        -6.3720, -6.5230, -6.6740, -6.8240, -6.9740, -7.1230, -7.2710, -7.4190,
    ),
}


def lens_table(n_beams, system_type, large_lens):
    """Key of BEAM_ANGLES for the beam count, sonar type and lens."""
    if large_lens and n_beams in (48, 96):
        return f"telephoto_{n_beams}"
    if n_beams in (64, 128):
        return f"high_{n_beams}"
    if n_beams in (48, 96):
        return f"standard_{n_beams}"
    raise ArisError(f"no beam angle table for {n_beams} beams")


def read_frame_header(data):
    h = {name: struct.unpack_from("<" + fmt, data, off)[0] for off, (name, fmt) in FRAME_FIELDS.items()}
    if h["version"] != SIGNATURE:
        raise ArisError(f"bad frame signature {h['version']:#x}")
    return h


class ArisFile:
    """Read-only .aris recording; frames are read on demand."""

    def __init__(self, path):
        self.path = path
        self._f = open(path, "rb")
        head = self._f.read(2 * HEADER_SIZE)
        if len(head) < 2 * HEADER_SIZE or struct.unpack_from("<I", head, 0)[0] != SIGNATURE:
            self._f.close()
            raise ArisError(f"{path}: not a .aris file")
        h = read_frame_header(head[HEADER_SIZE:])
        self.n_beams = beams_from_ping_mode(h["ping_mode"])
        self.n_samples = h["samples_per_beam"]
        self.frame_size = HEADER_SIZE + self.n_beams * self.n_samples
        self.n_frames = (os.path.getsize(path) - HEADER_SIZE) // self.frame_size
        self.model = SYSTEM_TYPES.get(h["system_type"], f"type {h['system_type']}")
        self.serial = h["serial"]
        self.lens = lens_table(self.n_beams, h["system_type"], h["large_lens"])
        # our convention: columns in increasing angle, so beam 0 (right-most, largest angle) goes last
        self.beam_angles = np.deg2rad(np.asarray(BEAM_ANGLES[self.lens][::-1], dtype=np.float64))
        self.first = h

    def header(self, i):
        self._seek(i)
        return read_frame_header(self._f.read(HEADER_SIZE))

    def frame(self, i):
        """Frame i as an opensonar.frame.Frame."""
        self._seek(i)
        raw = self._f.read(self.frame_size)
        if len(raw) < self.frame_size:
            raise ArisError(f"frame {i} is truncated")
        h = read_frame_header(raw)
        if h["samples_per_beam"] != self.n_samples or beams_from_ping_mode(h["ping_mode"]) != self.n_beams:
            raise ArisError(f"frame {i}: the frame size changes inside the file")
        data = np.frombuffer(raw, np.uint8, offset=HEADER_SIZE).reshape(self.n_samples, self.n_beams)
        if not h["reordered"]:
            raise ArisError(f"frame {i}: samples are not reordered (live sonar data is not supported here)")
        c = h["sound_speed"]
        start = h["sample_start_delay"] * 1e-6 * c / 2
        step = h["sample_period"] * 1e-6 * c / 2
        stamp = h["frame_time"] or h["pc_time"]
        return Frame(number=h["index"], timestamp=stamp * 1e-6, intensity=data[:, ::-1].copy(),
                     ranges=start + step * np.arange(self.n_samples), beam_angles=self.beam_angles,
                     sound_speed=float(c))

    def _seek(self, i):
        if not 0 <= i < self.n_frames:
            raise ArisError(f"frame {i} out of range 0..{self.n_frames - 1}")
        self._f.seek(HEADER_SIZE + i * self.frame_size)

    def close(self):
        self._f.close()
