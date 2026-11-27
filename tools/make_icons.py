"""Draw the app icon: a house roofline strung with glowing bulbs on a night sky.

    python3 tools/make_icons.py

Writes goveelights/web/icons/: icon.svg (any size), icon-192.png and icon-512.png (the
installed app, Android/desktop), apple-touch-icon.png (180 px, iPhone home screen).
Standard library only: the PNGs are drawn pixel by pixel with smooth (anti-aliased) edges
and written with zlib. The picture fills the whole square (no transparency, iOS adds its
own rounded corners) and keeps everything that matters inside the middle 80 % circle, so
Android can crop it to a circle or a squircle ("maskable") without cutting off bulbs.
Re-run it after changing the drawing below; commit the files it writes.
"""
from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "goveelights" / "web" / "icons"

# the drawing, in units of the icon's width (0..1, y downwards)
SKY_TOP, SKY_BOTTOM = (0x1b, 0x27, 0x45), (0x0b, 0x0e, 0x13)   # bottom = the app's --bg
GROUND = (0x10, 0x15, 0x1e)
WALL, ROOF = (0x22, 0x2c, 0x3c), (0x31, 0x3d, 0x55)
WINDOW, DOOR = (0xff, 0xc8, 0x6e), (0x3b, 0x2c, 0x1e)
PEAK, LEFT, RIGHT = (0.50, 0.25), (0.15, 0.535), (0.85, 0.535)
ROOF_POLY = [PEAK, RIGHT, LEFT]
WALLS = (0.245, 0.50, 0.755, 0.80)                 # x0, y0, x1, y1
WINDOWS = [(0.315, 0.585, 0.425, 0.675), (0.575, 0.585, 0.685, 0.675)]
DOOR_BOX = (0.462, 0.640, 0.538, 0.80)
GROUND_Y = 0.80
STARS = [(0.18, 0.15, 0.006), (0.30, 0.09, 0.004), (0.73, 0.12, 0.005), (0.85, 0.24, 0.004),
         (0.62, 0.07, 0.003), (0.10, 0.32, 0.003)]
BULB_COLOURS = [(0xff, 0x52, 0x52), (0xff, 0xc9, 0x4d), (0x3e, 0xe0, 0x7a), (0x4d, 0xa6, 0xff)]
BULB_R, GLOW_R = 0.026, 0.05
N_BULBS = 11


def bulbs():
    """Evenly spaced along the two roof edges, peak included (index 5)."""
    out = []
    half = N_BULBS // 2
    for k in range(N_BULBS):
        a, b, t = (LEFT, PEAK, k / half) if k <= half else (PEAK, RIGHT, (k - half) / half)
        out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, BULB_COLOURS[k % len(BULB_COLOURS)]))
    return out


BULBS = bulbs()


# ------------------------------------------------------------------ raster
def _poly_sd(x, y, poly):
    """Signed distance to a convex polygon given clockwise in y-down space (negative inside;
    exact inside, close enough outside for edge smoothing)."""
    d = -1e9
    n = len(poly)
    for i in range(n):
        (x0, y0), (x1, y1) = poly[i], poly[(i + 1) % n]
        ex, ey = x1 - x0, y1 - y0
        ln = math.hypot(ex, ey)
        d = max(d, ((x - x0) * ey - (y - y0) * ex) / ln)
    return d


def _box_sd(x, y, b):
    x0, y0, x1, y1 = b
    return max(x0 - x, x - x1, y0 - y, y - y1)


def _mix(c, d, a):
    return tuple(c[i] + (d[i] - c[i]) * a for i in range(3))


def _cov(sd, px):
    return min(1.0, max(0.0, 0.5 - sd / px))


def pixel(x, y, px):
    t = min(1.0, max(0.0, y / 0.85))
    c = _mix(SKY_TOP, SKY_BOTTOM, t)
    for sx, sy, sr in STARS:
        a = _cov(math.hypot(x - sx, y - sy) - sr, px)
        if a:
            c = _mix(c, (0xdd, 0xe6, 0xf5), 0.8 * a)
    c = _mix(c, GROUND, _cov(GROUND_Y - y, px))
    c = _mix(c, WALL, _cov(_box_sd(x, y, WALLS), px))
    for w in WINDOWS:
        c = _mix(c, WINDOW, 0.9 * _cov(_box_sd(x, y, w), px))
    c = _mix(c, DOOR, _cov(_box_sd(x, y, DOOR_BOX), px))
    c = _mix(c, ROOF, _cov(_poly_sd(x, y, ROOF_POLY), px))
    glow = [0.0, 0.0, 0.0]
    for bx, by, col in BULBS:
        d = math.hypot(x - bx, y - by)
        g = 0.6 * math.exp(-(d / GLOW_R) ** 2)
        for i in range(3):
            glow[i] += col[i] * g
    c = tuple(min(255.0, c[i] + glow[i]) for i in range(3))
    for bx, by, col in BULBS:
        d = math.hypot(x - bx, y - by)
        a = _cov(d - BULB_R, px)
        if a:
            hot = _mix(col, (255, 255, 255), max(0.0, 0.75 - d / BULB_R) * 0.9)   # a bright centre
            c = _mix(c, hot, a)
    return c


def png(size: int) -> bytes:
    px = 1.0 / size
    rows = bytearray()
    for j in range(size):
        rows.append(0)                                   # filter: none
        y = (j + 0.5) * px
        for i in range(size):
            r, g, b = pixel((i + 0.5) * px, y, px)
            rows += bytes((int(r + 0.5), int(g + 0.5), int(b + 0.5)))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(rows), 9)) + chunk(b"IEND", b""))


# ------------------------------------------------------------------ vector
def svg() -> str:
    S = 512

    def n(v):
        return f"{v * S:.1f}".rstrip("0").rstrip(".")

    def hx(c):
        return "#%02x%02x%02x" % c

    def box(b, fill, op=None):
        x0, y0, x1, y1 = b
        o = f' fill-opacity="{op}"' if op else ""
        return f'<rect x="{n(x0)}" y="{n(y0)}" width="{n(x1 - x0)}" height="{n(y1 - y0)}" fill="{hx(fill)}"{o}/>'

    defs = [f'<linearGradient id="sky" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{hx(SKY_TOP)}"/>'
            f'<stop offset=".85" stop-color="{hx(SKY_BOTTOM)}"/></linearGradient>']
    for k, col in enumerate(BULB_COLOURS):
        defs.append(f'<radialGradient id="g{k}"><stop offset="0" stop-color="{hx(col)}" stop-opacity=".6"/>'
                    f'<stop offset=".5" stop-color="{hx(col)}" stop-opacity=".22"/>'
                    f'<stop offset="1" stop-color="{hx(col)}" stop-opacity="0"/></radialGradient>')
        defs.append(f'<radialGradient id="b{k}"><stop offset="0" stop-color="#fff"/>'
                    f'<stop offset=".75" stop-color="{hx(col)}"/></radialGradient>')
    body = [f'<rect width="{S}" height="{S}" fill="url(#sky)"/>']
    body += [f'<circle cx="{n(x)}" cy="{n(y)}" r="{n(r)}" fill="#dde6f5" fill-opacity=".8"/>' for x, y, r in STARS]
    body.append(box((0, GROUND_Y, 1, 1), GROUND))
    body.append(box(WALLS, WALL))
    body += [box(w, WINDOW, ".9") for w in WINDOWS]
    body.append(box(DOOR_BOX, DOOR))
    body.append('<polygon points="' + " ".join(f"{n(x)},{n(y)}" for x, y in ROOF_POLY) + f'" fill="{hx(ROOF)}"/>')
    for k, (x, y, col) in enumerate(BULBS):
        g = BULB_COLOURS.index(col)
        body.append(f'<circle cx="{n(x)}" cy="{n(y)}" r="{n(GLOW_R * 2.2)}" fill="url(#g{g})"/>')
    for k, (x, y, col) in enumerate(BULBS):
        g = BULB_COLOURS.index(col)
        body.append(f'<circle cx="{n(x)}" cy="{n(y)}" r="{n(BULB_R)}" fill="url(#b{g})"/>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{S}" height="{S}" viewBox="0 0 {S} {S}">\n<title>House Lights</title>\n'
            f'<defs>{"".join(defs)}</defs>\n' + "\n".join(body) + "\n</svg>\n")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "icon.svg").write_text(svg(), encoding="utf-8", newline="\n")
    for name, size in (("icon-192.png", 192), ("icon-512.png", 512), ("apple-touch-icon.png", 180)):
        (OUT / name).write_bytes(png(size))
        print("wrote", OUT / name)
    print("wrote", OUT / "icon.svg")


if __name__ == "__main__":
    main()
