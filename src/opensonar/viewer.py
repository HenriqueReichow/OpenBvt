"""Live viewer of the XY fan (matplotlib), for any API source (sonar, emulator, .son).

Acquisition runs in a thread (`Head.stream`); the window always draws the latest ping, so it never
builds up a delay. `on_ping` sees every ping (lossless .son/NPZ recording).

Keys: space pause · ↑/↓ range +/−20 % (live) · c colormap · l log/linear scale ·
g grid · s save PNG · q quit.
"""
import os
import time

import numpy as np

COLORMAPS = ("gray", "copper", "viridis", "inferno")


def extent(geo):
    """(x_min, x_max, y_min, y_max) in metres of the XY image, for `imshow(extent=…)`."""
    res = float(geo["res"])
    x0 = -geo["origin_col"] * res
    y1 = geo["origin_row"] * res
    return (x0, x0 + geo["cols_xy"] * res, y1 - geo["rows_xy"] * res, y1)


def crop_to_range(img, geo, r):
    """Crops the XY image (sized for the head's maximum range) to the pixels within
    radius `r` (m) of the sonar. Pixel values are unchanged; returns (image, geometry)."""
    res = float(geo["res"])
    inside = geo["X"] ** 2 + geo["Y"] ** 2 <= (float(r) + res) ** 2
    rows, cols = np.flatnonzero(inside.any(1)), np.flatnonzero(inside.any(0))
    if rows.size == 0:
        return img, geo
    r0, r1, c0, c1 = rows[0], rows[-1] + 1, cols[0], cols[-1] + 1
    return img[r0:r1, c0:c1], dict(geo, X=geo["X"][r0:r1, c0:c1], Y=geo["Y"][r0:r1, c0:c1],
                                   rows_xy=r1 - r0, cols_xy=c1 - c0,
                                   origin_row=geo["origin_row"] - r0, origin_col=geo["origin_col"] - c0)


def for_display(img, log=True):
    """u16 → float for the screen (log1p or linear), with the maximum at 1."""
    v = np.log1p(img.astype(np.float32)) if log else img.astype(np.float32)
    m = float(v.max())
    return v / m if m > 0 else v


class Viewer:
    def __init__(self, sonar, head=0, on_ping=None, real_time=None, png_dir=".", show=True):
        import matplotlib
        if not show:
            matplotlib.use("Agg", force=True)
        import matplotlib.pyplot as plt
        self.plt = plt
        self.show = show
        self.sonar, self.head = sonar, sonar.head(head)
        self.png_dir = png_dir
        self.log, self.grid, self.paused = True, True, False
        self.i_cmap = 0
        self.shown, self.last_ping, self.last_image, self.last_geo = 0, None, None, None
        self._t_shown = []
        rt = (not sonar.live) if real_time is None else real_time
        self.stream = self.head.stream(real_time=rt, on_ping=on_ping)
        self.fig, self.ax = plt.subplots(figsize=(12, 7))
        self.fig.canvas.manager.set_window_title(f"opensonar — {sonar.model} head {self.head.index}")
        self.ax.set_facecolor("black")
        self.ax.set_xlabel("x (m)")
        self.ax.set_ylabel("y (m)")
        self._im = None
        self._grid = []
        self.fig.canvas.mpl_connect("key_press_event", self._key)

    # --- drawing ---
    def _draw_grid(self, geo, ping, img=None):
        for a in self._grid:
            a.remove()
        self._grid = []
        if not self.grid:
            return
        if img is not None and "X" in geo and (img > 0).any():
            self._fan_radius = float(np.sqrt(geo["X"] ** 2 + geo["Y"] ** 2)[img > 0].max())   # real fan radius
        r_max = getattr(self, "_fan_radius", None) or float(ping.range_stop)
        step = next(p for p in (0.5, 1, 2, 5, 10, 20, 25, 50) if r_max / p <= 8)
        ang = np.linspace(-np.pi / 2, np.pi / 2, 181)
        fov = geo.get("fov")
        lim = max(abs(float(fov[0])), abs(float(fov[1]))) if fov is not None else np.pi / 2
        for r in np.arange(step, r_max + 1e-9, step):
            m = np.abs(ang) <= lim
            ln, = self.ax.plot(r * np.sin(ang[m]), r * np.cos(ang[m]), color="#3a7", lw=0.5, alpha=0.6)
            tx = self.ax.text(0, r, f"{r:g} m", color="#3a7", fontsize=7, ha="center", va="bottom")
            self._grid += [ln, tx]
        for a in np.deg2rad(np.arange(-90, 91, 15)):
            if abs(a) <= lim:
                ln, = self.ax.plot([0, r_max * np.sin(a)], [0, r_max * np.cos(a)], color="#3a7", lw=0.4, alpha=0.4)
                self._grid.append(ln)

    def update(self, timeout=0.05):
        """Takes the latest ping (if any) and redraws. Returns the ping shown, or None."""
        if self.paused:
            return None
        p = self.stream.latest(timeout=timeout)
        if p is None:
            if self.stream.error:
                raise self.stream.error
            return None
        img, geo = crop_to_range(*self.head.image(p, "xy"), p.range_stop)
        v = for_display(img, self.log)
        ext = extent(geo)
        if self._im is None or self._im.get_extent() != list(ext) or self._im.get_array().shape != v.shape:
            if self._im is not None:
                self._im.remove()
            self._im = self.ax.imshow(v, extent=ext, cmap=COLORMAPS[self.i_cmap], vmin=0, vmax=1,
                                      origin="upper", interpolation="nearest")
            self.ax.set_xlim(ext[0], ext[1])
            self.ax.set_ylim(0, ext[3])
            self.ax.set_aspect("equal")
            self._draw_grid(geo, p, img)
        else:
            self._im.set_data(v)
        self.shown += 1
        now = time.time()
        self._t_shown = [t for t in self._t_shown if now - t < 3] + [now]
        fps = (len(self._t_shown) - 1) / max(now - self._t_shown[0], 1e-6) if len(self._t_shown) > 1 else 0.0
        clock = time.strftime("%H:%M:%S", time.localtime(p.timestamp)) + f".{int(p.timestamp % 1 * 1000):03d}"
        self.ax.set_title(f"{self.head.model} — ping {p.number}  {clock}  "
                          f"{p.range_start:.1f}–{p.range_stop:.1f} m   "
                          f"received {self.stream.received}  screen {fps:.1f} fps"
                          f"{'  [PAUSED]' if self.paused else ''}", fontsize=10)
        self.last_ping, self.last_image, self.last_geo = p, img, geo
        return p

    # --- keyboard ---
    def _key(self, ev):
        k = ev.key
        if k == " ":
            self.paused = not self.paused
        elif k in ("up", "down") and self.sonar.live and self.last_ping is not None:
            lo, hi = self.head.limits
            stop = float(self.last_ping.range_stop) * (1.2 if k == "up" else 1 / 1.2)
            self.head.adjust_range(lo, min(max(stop, lo + 0.5), hi))
        elif k == "c" and self._im is not None:
            self.i_cmap = (self.i_cmap + 1) % len(COLORMAPS)
            self._im.set_cmap(COLORMAPS[self.i_cmap])
        elif k == "l":
            self.log = not self.log
        elif k == "g" and self.last_ping is not None:
            self.grid = not self.grid
            self._draw_grid(self.last_geo, self.last_ping)
        elif k == "s" and self.last_image is not None:
            os.makedirs(self.png_dir, exist_ok=True)
            path = os.path.join(self.png_dir, f"fan_{self.last_ping.number:06d}.png")
            from PIL import Image
            Image.fromarray(self.last_image).save(path)          # u16, lossless
            print(f"saved {path}")
        elif k == "q":
            self.plt.close(self.fig)

    # --- loop ---
    def run(self, n_shown=None):
        """Window loop until closed, the source ends, `q`, or `n_shown` frames."""
        try:
            while self.plt.fignum_exists(self.fig.number):
                if n_shown is not None and self.shown >= n_shown:
                    break
                if self.update() is None and not self.stream.active and self.stream.latest(0, only_new=False) is None:
                    break                                       # end of the file
                if self.show:
                    self.plt.pause(0.001)
        finally:
            self.close()
        return self.shown

    def close(self):
        self.stream.stop()
        if self.plt.fignum_exists(self.fig.number):
            self.plt.close(self.fig)
