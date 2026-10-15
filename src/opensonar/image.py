"""R-Theta and XY image generator, from the frequency-steering principle (see docs/IMAGING.md).

Pipeline (everything from the file configuration, no tuned parameters):
  1. automatic resolution policy -> hop (samples/frame) and number of frames;
  2. per transducer: Hann STFT (256 points), |X| of bins 1..128 x per-beam gain,
     truncated to u16, shifted per bin by the chirp delay (spectrogram of the replica);
  3. per-pixel map: frequency-steering geometry (f = c / (4 d sin θ)),
     up to 2 sectors per pixel with blending weights in 1/32;
  4. rendering and 3x3 filter (default filter flags = 3).
"""
import hashlib
import threading
from collections import OrderedDict

import numpy as np

N_BEAMS = 128


def hann_window(n):
    """Hann without the zero end points: 0.5 (1 - cos(2 pi i / (n + 1))), i = 1..n."""
    i = np.arange(1, n + 1)
    return 0.5 * (1.0 - np.cos(2.0 * np.pi * i / (n + 1)))


def beam_gain(table, n_beams=N_BEAMS):
    """Resamples the head gain table to n_beams points.

    Triangular-kernel regression with half-width h = 2.5/M (M = table size): each point i/M
    contributes to the targets j/n_beams with weight 1 - |dx|/h, normalized by the sum of the
    weights.
    """
    if table.size == 0:
        return np.ones(n_beams, np.float32)
    m = table.size
    xs = np.arange(m, dtype=np.float32) / np.float32(m)
    xd = np.arange(n_beams, dtype=np.float32) / np.float32(n_beams)
    h = np.float32(2.5 / m)
    dx = abs(xd[1] - xd[0])
    acc = np.zeros(n_beams, np.float32)
    weight = np.zeros(n_beams, np.float32)
    for xi, vi in zip(xs, table.astype(np.float32)):
        j0 = max(int(np.floor((xi - xd[0] - h) / dx)), 0)
        j1 = min(int(np.ceil((xi - xd[0] + h) / dx)), n_beams)
        for j in range(j0, j1):
            w = np.float32(1.0) - abs(xd[j] - xi) / h
            acc[j] += w * vi
            weight[j] += w
    return np.where(weight > 0, acc / np.where(weight > 0, weight, 1), acc)


N_FFT = 2 * N_BEAMS
F32 = np.float32


def auto_hop(range_start, range_stop):
    """Hop (samples per frame) of the automatic resolution policy."""
    span = F32(range_stop) - F32(range_start)
    if span >= 100.0:
        return 0xA6
    if span <= 5.0:
        return 0x19
    return int(((span - F32(5.0)) * F32(0.010526316) * F32(0.54999995) + F32(0.1)) * F32(256.0))


def head_fs(head):
    """Signal sampling rate (Hz): u32_a of HEAD_SETTINGS. The two transducer u32 values are the
    sector's upper/lower frequencies; in newer files the upper one equals fs."""
    return head.raw["u32_a"]


def _decimation(head, tr):
    return head.raw["u32_b"] // head_fs(head)


def replica_bin_delay(head, tr):
    """Per-bin delay (in units of the scale) and scale (s), from the replica spectrogram."""
    dec = _decimation(head, tr)
    rep = head.pulse.real.astype(F32)
    n = rep.size // dec
    x = np.concatenate([np.zeros(N_BEAMS, F32), rep[0:n * dec:dec], np.zeros(N_BEAMS, F32)])
    w = hann_window(N_FFT).astype(F32)
    hop = x.size // n
    power = np.zeros((N_BEAMS, n), F32)
    for k in range(n):
        seg = np.zeros(N_FFT, F32)
        s = x[k * hop:k * hop + N_FFT]
        seg[:s.size] = s
        power[:, k] = np.abs(np.fft.rfft(seg * w)[1:N_BEAMS + 1]) ** 2
    scale = (rep.size / head.raw["u32_b"]) / n
    return power.argmax(1) - n, scale


def expected_samples(head, ping, tr):
    """Number of samples the ping range requires: (stop - start) * 2 / c * fs."""
    return int((ping.range_stop - ping.range_start) * 2.0 / head.sound_speed * head_fs(head))


def centered_signal(head, ping, t):
    """2048 - sample, zero-padded at the end if the recording is shorter than the range."""
    x = F32(2048.0) - ping.signals[t].astype(F32)
    n = expected_samples(head, ping, head.transducers[t])
    if x.size < n:
        x = np.concatenate([x, np.zeros(n - x.size, F32)])
    return x


def sector_profile(head, ping, t, frames, frame_rate):
    """u16 profile (beams x frames) of transducer t"""
    tr = head.transducers[t]
    x = centered_signal(head, ping, t)
    w = hann_window(N_FFT).astype(F32)
    gain = beam_gain(head.normalization).astype(F32)
    delay, scale = replica_bin_delay(head, tr)
    shift = np.rint(delay * scale * frame_rate).astype(np.int64)
    hop = x.size // frames
    seg = np.zeros((frames, N_FFT), F32)
    for k in range(frames):
        s = x[k * hop:k * hop + N_FFT]
        seg[k, :s.size] = s
    mag = np.abs(np.fft.rfft(seg * w, axis=1)[:, 1:N_BEAMS + 1]).astype(F32) * gain   # frames x beams
    mag = np.minimum(mag, F32(65535.0)).astype(np.int64)
    out = np.zeros(N_BEAMS * frames, np.uint16)
    base = frames * np.arange(N_BEAMS) - shift
    for k in range(frames):           # outer frame, inner bin
        idx = base + k
        ok = (idx >= 0) & (idx < out.size)
        out[idx[ok]] = mag[k, ok]
    return out.reshape(N_BEAMS, frames)


def _sector_limits(head, tr):
    """Local angles (rad) at the extreme frequencies, with the sector sign."""
    s = -1.0 if tr.flags in (1, 0x11) else 1.0
    c, d = F32(head.sound_speed), F32(head.raw["f_a"])
    a_hi = np.arcsin(c / (F32(4.0) * d * F32(tr.high_freq)))
    a_lo = np.arcsin(c / (F32(4.0) * d * F32(tr.low_freq)))
    v = sorted((s * a_hi, s * a_lo))
    return s, F32(v[0]), F32(v[1])


def rtheta_geometry(head, ping):
    """Frames, frame rate, start (s), resolution, number of rows and angular grid of the image."""
    fs = head_fs(head)
    hop = auto_hop(ping.range_start, ping.range_stop)
    frames = centered_signal(head, ping, 0).size // hop
    frame_rate = fs / hop
    pulse_duration = F32(head.pulse.size) / F32(head.raw["u32_b"])
    start = F32(2.0 * ping.range_start / head.sound_speed) + (F32(N_FFT // 2) / F32(fs) - pulse_duration)
    res = F32((ping.range_stop - ping.range_start) / frames)
    frame_shift = int(np.rint(start * frame_rate))
    if mapping_method(head) == 1:
        lims = []
        for tr in head.transducers:
            s, lo, hi = _sector_limits(head, tr)
            rot = np.deg2rad(tr.rotation[2])
            lims += [rot + lo, rot + hi]
        fov = (min(lims), max(lims))
    else:
        sec0 = method0_sectors(head)
        fov = (sec0[-1][0], sec0[0][1])
    cols = N_BEAMS * len(head.transducers)
    ang_step = (fov[1] - fov[0]) / cols
    angles = fov[0] + ang_step * np.arange(cols)
    rows = frames
    ranges = (rows - 1 - np.arange(rows) + frame_shift + 1) * res
    return dict(frames=frames, frame_rate=frame_rate, start=start, res=res, frame_shift=frame_shift,
                rows=rows, cols=cols, angles=angles, ranges=ranges, fov=fov)


def rtheta_coordinates(geo):
    """(x, y) coordinates in metres of each R-Theta pixel."""
    R = geo["ranges"][:, None].astype(F32)
    th = geo["angles"][None, :].astype(F32)
    return R * np.sin(th), R * np.cos(th)


def rtheta_map(head, ping, geo, X=None, Y=None):
    """Per pixel: up to 2 entries (sector, beam, frame) and weights in 1/32.

    X, Y: pixel coordinates (default: the R-Theta grid of geo)."""
    if X is None:
        X, Y = rtheta_coordinates(geo)
    H, W = X.shape
    c, d = F32(head.sound_speed), F32(head.raw["f_a"])
    rmin, rmax = F32(head.range_min), F32(head.range_max)
    n = len(head.transducers)
    cand = []                                          # per sector: (valid, beam, frame, radius)
    for tr in head.transducers:
        s, lo, hi = _sector_limits(head, tr)
        rot = F32(np.deg2rad(tr.rotation[2]))
        dx, dy = X - F32(tr.position[0]), Y - F32(tr.position[1])
        xr = np.cos(rot) * dx - np.sin(rot) * dy
        yr = np.sin(rot) * dx + np.cos(rot) * dy
        rl = np.sqrt(xr * xr + yr * yr)
        ang = np.arctan2(xr, yr).astype(F32)
        fs = F32(head_fs(head))
        with np.errstate(all="ignore"):
            f = c / (F32(4.0) * d * np.sin(s * ang).astype(F32))
            beam = N_BEAMS - np.rint((f - fs / 2) / (fs / N_FFT))
        frame = np.rint(rl / geo["res"]) - geo["frame_shift"]
        ok = (rl >= rmin) & (rl <= rmax) & (ang >= lo) & (ang <= hi) & (beam > 0) & (beam < N_BEAMS) \
            & (frame >= 0) & (frame < geo["frames"])
        cand.append((ok, np.nan_to_num(beam).astype(np.int64), frame.astype(np.int64), rl))
    # Scan: per row, start at the last sector (even row) or the first (odd row);
    # for each pixel walk the sectors circularly from the start point and keep up to 2; the start
    # point becomes the last sector found.
    ok_all = np.stack([cd[0] for cd in cand])           # n x H x W
    beam_all = np.stack([cd[1] for cd in cand])
    frame_all = np.stack([cd[2] for cd in cand])
    ent = np.full((H, W, 2), -1, np.int64)
    beams = np.zeros((H, W, 2), np.int64)
    count = np.zeros((H, W), np.int64)
    for r in range(H):
        start = n - 1 if r % 2 == 0 else 0
        okr = ok_all[:, r, :]
        for col in np.where(okr.any(0))[0]:
            found = 0
            t = start
            for _ in range(n):
                if t >= n:
                    t = 0
                if okr[t, col]:
                    ent[r, col, found] = (t << 22) + frame_all[t, r, col] * N_BEAMS + beam_all[t, r, col]
                    beams[r, col, found] = beam_all[t, r, col]
                    found += 1
                    start = t
                    if found > 1:
                        break
                t += 1
            count[r, col] = found
    # weights: beam favoured by the normalized range; near=0, far=128, exponents 1
    radius = np.sqrt(X * X + Y * Y)
    byte_r = (np.rint(F32(256.0) * (radius / rmax)).astype(np.int64)) & 0xFF
    fav = (byte_r.astype(F32) * F32(1.0 / 256.0) * F32(128.0)).astype(np.int64)
    diff = (beams & 0xFF) - fav[..., None]
    diff[diff == 0] = 1
    w = F32(1.0) / np.abs(diff).astype(F32)           # 1/|d| in float32
    inv = F32(1.0) / w.sum(-1, keepdims=True).astype(F32)
    # product in extended precision (x87) and truncation (rounding mode = truncate)
    weights = np.trunc(32.0 * w.astype(np.float64) * inv.astype(np.float64)).astype(np.int64)
    return ent, count, weights


def sector_box(head, n_points=2001):
    """Bounding box (x_min, x_max, y_min, y_max) of the sector fans, with the head range."""
    xs, ys = [], []
    for tr in head.transducers:
        _, lo, hi = _sector_limits(head, tr)
        rot = np.deg2rad(tr.rotation[2])
        a = np.linspace(lo, hi, n_points)
        for r in (head.range_min, head.range_max):
            xl, yl = r * np.sin(a), r * np.cos(a)
            xs.append(np.cos(rot) * xl + np.sin(rot) * yl + tr.position[0])
            ys.append(-np.sin(rot) * xl + np.cos(rot) * yl + tr.position[1])
    xs, ys = np.concatenate(xs), np.concatenate(ys)
    return xs.min(), xs.max(), ys.min(), ys.max()


def xy_geometry(head, ping, crop=False):
    """Size, origin and pixel coordinates of the XY (fan) image.
    crop=True keeps only the window of pixels within the ping's stop range (the full image covers
    the head's maximum range): the same pixels as the full image, without computing the rest."""
    geo = rtheta_geometry(head, ping)
    res = geo["res"]
    if mapping_method(head) == 1:
        xmin, xmax, ymin, ymax = sector_box(head)
    else:                                # method 0
        half = geo["fov"][1]
        xmax = float(np.sin(half) * head.range_max)
        xmin, ymin, ymax = -xmax, 0.0, float(head.range_max)
    cols = int(np.ceil((xmax - xmin) / res))
    rows = int(np.ceil((ymax - ymin) / res))
    origin_col = int(np.trunc(-xmin / res))
    origin_row = int(np.trunc(ymax / res))
    r0, c0 = 0, 0
    if crop:
        k = int(np.ceil(float(ping.range_stop) / res)) + 1
        r0, c0 = max(origin_row - k, 0), max(origin_col - k, 0)
        rows, cols = min(origin_row + k + 1, rows) - r0, min(origin_col + k + 1, cols) - c0
        origin_row, origin_col = origin_row - r0, origin_col - c0
    cc = np.arange(cols)[None, :]
    rr = np.arange(rows)[:, None]
    X = ((cc - origin_col) * res).astype(F32) * np.ones((rows, 1), F32)
    Y = ((origin_row - rr) * res).astype(F32) * np.ones((1, cols), F32)
    return dict(geo, rows_xy=rows, cols_xy=cols, origin_col=origin_col, origin_row=origin_row, X=X, Y=Y)


def rtheta(head, ping, smooth=True):
    """u16 R-Theta image (3x3 filter included; smooth=False skips the filter)."""
    geo = rtheta_geometry(head, ping)
    return _render(head, ping, geo, *rtheta_coordinates(geo), smooth=smooth), geo


def xy(head, ping, smooth=True, crop=False):
    """u16 XY (fan) image; row 0 = maximum y, sonar origin at geo['origin_row'/'origin_col'].
    smooth=False skips the 3x3 mean ; crop=True renders only the
    window within the ping's stop range (much faster when the range is short, see xy_geometry)."""
    geo = xy_geometry(head, ping, crop)
    return _render(head, ping, geo, geo["X"], geo["Y"], smooth=smooth), geo


def mapping_method(head):
    """Image mapping method: 1 if the first transducer has its own orientation
    (record 0x11 subtype 6); 0 (older sector layout) otherwise."""
    return 1 if head.transducers and head.transducers[0].oriented else 0


def _absolute_limits(head, tr, invert=False):
    """(inner, outer) in rad: asin(c / (4 d f)) at the two transducer frequencies."""
    c, d = F32(head.sound_speed), F32(head.raw["f_a"])
    a = np.arcsin(c / (F32(4.0) * d * F32(tr.high_freq)))
    b = np.arcsin(c / (F32(4.0) * d * F32(tr.low_freq)))
    return (b, a) if invert else (a, b)


def method0_sectors(head, invert=False):
    """Side-by-side sectors centred on 0: [(θ0, θ1, transducer)], from the largest angle down."""
    widths = [abs(o - i) for i, o in (_absolute_limits(head, tr, invert) for tr in head.transducers)]
    current = F32(sum(widths) / 2)
    out = []
    for w, tr in zip(widths, head.transducers):
        out.append((F32(current - w), F32(current), tr))
        current = F32(current - w)
    return out


def method0_map(head, ping, geo, X, Y, invert=False):
    """Method 0 map: one sector per pixel, no rotation and no blending."""
    H, W = X.shape
    c, d = F32(head.sound_speed), F32(head.raw["f_a"])
    R = np.sqrt(X * X + Y * Y).astype(F32)
    th = np.arctan2(X, Y).astype(F32)
    rmin, rmax = F32(head.range_min), F32(head.range_max)
    ent = np.full((H, W, 2), -1, np.int64)
    count = np.zeros((H, W), np.int64)
    free = np.ones((H, W), bool)
    for t, (t0, t1, tr) in enumerate(method0_sectors(head, invert)):
        inside = free & (th >= t0) & (th <= t1)
        free &= ~inside                      # the first sector containing the angle takes the pixel
        inner, outer = _absolute_limits(head, tr, invert)
        if tr.flags in (0, 0x10):
            phi = inner + (th - t0)
        else:
            phi = outer - (th - t0)
        fs = F32(head_fs(head))
        with np.errstate(all="ignore"):
            f = c / (F32(4.0) * d * np.sin(phi).astype(F32))
            beam = N_BEAMS - np.rint((f - fs / 2) / (fs / N_FFT))
        frame = np.rint(R / geo["res"]) - geo["frame_shift"]
        ok = inside & (R >= rmin) & (R <= rmax) & (beam > 0) & (beam < N_BEAMS) \
            & (frame >= 0) & (frame < geo["frames"])
        beam = np.nan_to_num(beam).astype(np.int64)
        ent[..., 0] = np.where(ok, (t << 22) + frame.astype(np.int64) * N_BEAMS + beam, ent[..., 0])
        count = np.where(ok, 1, count)
    return ent, count, np.zeros((H, W, 2), np.int64)


_MAPS = OrderedDict()      # geometric map cache: depends on configuration and range, not on the signal
_MAPS_MAX = 4
_MAPS_LOCK = threading.Lock()   # the window and the acquisition thread (NPZ) render at the same time


def _map_key(head, geo, X, Y):
    h = hashlib.blake2b(digest_size=16)
    trs = tuple((t.index, t.high_freq, t.low_freq, t.flags, tuple(t.position), tuple(t.rotation), t.oriented)
                for t in head.transducers)
    h.update(repr((mapping_method(head), head.sound_speed, float(head.raw["f_a"]), float(head.range_min),
                   float(head.range_max), trs, float(geo["res"]), int(geo["frames"]), X.shape)).encode())
    for v in (geo["frame_shift"], geo["ranges"], geo["angles"], X, Y):
        h.update(np.ascontiguousarray(v).tobytes())
    return h.digest()


def _map(head, ping, geo, X, Y):
    """(index into the concatenated profiles for the 2 entries, count, weights), cached."""
    key = _map_key(head, geo, X, Y)
    with _MAPS_LOCK:
        if key in _MAPS:
            _MAPS.move_to_end(key)
            return _MAPS[key]
    if mapping_method(head) == 1:
        ent, count, weights = rtheta_map(head, ping, geo, X, Y)
    else:
        ent, count, weights = method0_map(head, ping, geo, X, Y)
    F, n_tr = geo["frames"], len(head.transducers)
    null = n_tr * N_BEAMS * F                # position of a zero appended after the profiles
    gs = []
    for e in (ent[..., 0], ent[..., 1]):
        t = np.maximum(e >> 22, 0)
        idx = e & 0x3FFFFF
        g = t * (N_BEAMS * F) + (idx % N_BEAMS) * F + idx // N_BEAMS     # profile t, beam, frame
        gs.append(np.where((e >= 0) & (t < n_tr), g, null))
    value = (gs[0], gs[1], count, weights)
    with _MAPS_LOCK:
        _MAPS[key] = value
        while len(_MAPS) > _MAPS_MAX:
            _MAPS.popitem(last=False)
    return value


def _render(head, ping, geo, X, Y, smooth=True):
    F = geo["frames"]
    profiles = [sector_profile(head, ping, t, F, geo["frame_rate"]) for t in range(len(head.transducers))]
    g0, g1, count, weights = _map(head, ping, geo, X, Y)
    allp = np.concatenate([p.ravel() for p in profiles] + [np.zeros(1, np.uint16)]).astype(np.float32)
    v0, v1 = allp[g0], allp[g1]
    img = np.where(count == 1, v0, 0).astype(np.int64)
    two = count == 2
    acc = (v0 * (weights[..., 0] / F32(32.0))).astype(np.int64) + (v1 * (weights[..., 1] / F32(32.0))).astype(np.int64)
    img[two] = acc[two] & 0xFFFF
    img = img.astype(np.uint16)
    return box3x3(img) if smooth else img


def box3x3(img):
    """3x3 mean: integer sum of the 9 neighbours / 9; borders copied."""
    a = img.astype(np.int64)
    out = img.copy()
    s = sum(a[1 + dr:a.shape[0] - 1 + dr, 1 + dc:a.shape[1] - 1 + dc] for dr in (-1, 0, 1) for dc in (-1, 0, 1))
    out[1:-1, 1:-1] = (s // 9).astype(img.dtype)
    return out
