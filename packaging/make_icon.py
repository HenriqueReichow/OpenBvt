"""Draws the opensonar icon (the same design as opensonar.svg) as PNG at the given sizes."""
import math
import sys

from PIL import Image, ImageDraw


def icon(size):
    s = 4 * size                                           # draw large, then downsample (antialiasing)
    k = s / 256.0
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([8 * k, 8 * k, 248 * k, 248 * k], radius=44 * k, fill=(14, 27, 44, 255))
    ox, oy, R = 128 * k, 214 * k, 118 * k
    for i in range(int(R), 0, -1):                         # radial copper gradient
        t = i / R
        c = [(255, 224, 176), (224, 138, 60), (90, 42, 12)]
        a, b, u = (c[0], c[1], t / 0.45) if t < 0.45 else (c[1], c[2], (t - 0.45) / 0.55)
        col = tuple(int(a[j] + (b[j] - a[j]) * u) for j in range(3)) + (255,)
        d.pieslice([ox - i, oy - i, ox + i, oy + i], 270 - 65, 270 + 65, fill=col)
    line = (62, 36, 22, 255)                               # the dark lines of the SVG, blended
    for r in (50, 85):
        rr = r * k
        d.arc([ox - rr, oy - rr, ox + rr, oy + rr], 270 - 65, 270 + 65, fill=line, width=int(5 * k))
    for deg in (-35, 0, 35):
        t = math.radians(deg)
        d.line([ox, oy, ox + R * math.sin(t), oy - R * math.cos(t)], fill=line, width=int(5 * k))
    d.ellipse([ox - 9 * k, oy - 9 * k, ox + 9 * k, oy + 9 * k], fill=(255, 224, 176, 255))
    return img.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    out = sys.argv[1]
    for n in map(int, sys.argv[2:]):
        icon(n).save(f"{out}/opensonar_{n}.png")
