"""A small, fast 2D raster for hologram-fan clips (pure Python, stdlib only).

The image is kept as three planes (R, G, B), one bytearray each. Every shape is drawn as
horizontal spans, and every blend is a 256-entry lookup table applied to a whole span with
bytes.translate, so the per-pixel work happens in C. That is what makes 720 px clips at
24 fps practical without numpy.

Blend modes (per channel, v = what is there, c = the colour):
    set   v = c                       (opaque, fastest)
    over  v = v + (c - v) * alpha     (alpha blend)
    add   v = min(255, v + c * alpha) (light: glows, sparks)
    max   v = max(v, c * alpha)       (lighten: overlapping glows don't blow out)
    mul   v = v * alpha               (darken / fade; colour ignored)

Shapes are not anti-aliased: a POV fan draws ~100-500 LEDs across, far coarser than the
canvas, so hard edges cost nothing visible and keep strokes bold.
"""
from __future__ import annotations

import math

Color = tuple  # (r, g, b) 0..255

_TABLES: dict = {}


def _table(mode: str, c: int, a: float) -> bytes:
    aq = max(0, min(64, int(a * 64 + 0.5)))
    key = (mode, c, aq)
    t = _TABLES.get(key)
    if t is None:
        a = aq / 64
        if mode == "over":
            t = bytes(int(v + (c - v) * a + 0.5) for v in range(256))
        elif mode == "add":
            t = bytes(min(255, int(v + c * a + 0.5)) for v in range(256))
        elif mode == "max":
            k = int(c * a + 0.5)
            t = bytes(max(v, k) for v in range(256))
        elif mode == "mul":
            t = bytes(int(v * a + 0.5) for v in range(256))
        else:
            raise ValueError(f"unknown blend mode {mode!r}")
        if len(_TABLES) > 20000:
            _TABLES.clear()
        _TABLES[key] = t
    return t


def clamp8(v: float) -> int:
    return 0 if v <= 0 else 255 if v >= 255 else int(v + 0.5)


def scale(c: Color, k: float) -> Color:
    return (clamp8(c[0] * k), clamp8(c[1] * k), clamp8(c[2] * k))


def mix(a: Color, b: Color, t: float) -> Color:
    return (clamp8(a[0] + (b[0] - a[0]) * t), clamp8(a[1] + (b[1] - a[1]) * t), clamp8(a[2] + (b[2] - a[2]) * t))


class Canvas:
    def __init__(self, w: int, h: int):
        self.w, self.h = int(w), int(h)
        self.n = self.w * self.h
        self._zero = bytes(self.n)
        self.p = [bytearray(self.n), bytearray(self.n), bytearray(self.n)]

    # ------------------------------------------------------------ basics
    def clear(self):
        for pl in self.p:
            pl[:] = self._zero

    def span(self, y: int, x0: int, x1: int, c: Color, mode: str = "set", a: float = 1.0):
        """Fill pixels x0 <= x < x1 on row y (clipped)."""
        if y < 0 or y >= self.h:
            return
        if x0 < 0:
            x0 = 0
        if x1 > self.w:
            x1 = self.w
        if x1 <= x0:
            return
        o = y * self.w
        i, j = o + x0, o + x1
        if mode == "set" or (mode == "over" and a >= 0.999):
            n = j - i
            for k in range(3):
                self.p[k][i:j] = bytes((c[k],)) * n
            return
        if a <= 0.001 and mode != "mul":
            return
        for k in range(3):
            pl = self.p[k]
            pl[i:j] = pl[i:j].translate(_table(mode, c[k], a))

    def fade(self, k: float):
        """Darken the whole frame (k = 0..1)."""
        t = _table("mul", 0, k)
        for pl in self.p:
            pl[:] = pl.translate(t)

    # ------------------------------------------------------------ shapes
    def rect(self, x0, y0, x1, y1, c: Color, mode="set", a=1.0):
        ya, yb = int(math.floor(min(y0, y1) + 0.5)), int(math.floor(max(y0, y1) + 0.5))
        xa, xb = int(math.floor(min(x0, x1) + 0.5)), int(math.floor(max(x0, x1) + 0.5))
        for y in range(max(0, ya), min(self.h, yb)):
            self.span(y, xa, xb, c, mode, a)

    def circle(self, cx, cy, r, c: Color, mode="set", a=1.0):
        if r <= 0:
            return
        r2 = r * r
        ya, yb = int(math.floor(cy - r)), int(math.ceil(cy + r))
        for y in range(max(0, ya), min(self.h, yb + 1)):
            dy = y + 0.5 - cy
            d = r2 - dy * dy
            if d < 0:
                continue
            hw = math.sqrt(d)
            self.span(y, int(math.floor(cx - hw + 0.5)), int(math.floor(cx + hw + 0.5)), c, mode, a)

    def ring(self, cx, cy, r0, r1, c: Color, mode="set", a=1.0):
        """Annulus between radius r0 (inner) and r1 (outer)."""
        if r1 <= 0 or r1 <= r0:
            return
        ya, yb = int(math.floor(cy - r1)), int(math.ceil(cy + r1))
        for y in range(max(0, ya), min(self.h, yb + 1)):
            dy = y + 0.5 - cy
            d1 = r1 * r1 - dy * dy
            if d1 < 0:
                continue
            ho = math.sqrt(d1)
            d0 = r0 * r0 - dy * dy
            xa, xd = int(math.floor(cx - ho + 0.5)), int(math.floor(cx + ho + 0.5))
            if d0 <= 0 or r0 <= 0:
                self.span(y, xa, xd, c, mode, a)
            else:
                hi = math.sqrt(d0)
                xb, xc = int(math.floor(cx - hi + 0.5)), int(math.floor(cx + hi + 0.5))
                self.span(y, xa, xb, c, mode, a)
                self.span(y, xc, xd, c, mode, a)

    def glow(self, cx, cy, r, c: Color, a=1.0, steps=6, mode="max"):
        """Soft radial glow: stacked discs, bright in the middle and fading to the edge."""
        if r <= 0:
            return
        for k in range(steps):
            f = (k + 1) / steps                      # outer (dim) to inner (bright)
            rr = r * (1 - f) + r * 0.12 * f
            if mode == "max":
                self.circle(cx, cy, rr, c, "max", a * (f ** 1.6))
            else:
                self.circle(cx, cy, rr, c, mode, a / steps)

    def polygon(self, pts, c: Color, mode="set", a=1.0):
        """Filled polygon (even-odd), points as [(x, y), ...]."""
        n = len(pts)
        if n < 3:
            return
        ys = [p[1] for p in pts]
        ya, yb = max(0, int(math.floor(min(ys)))), min(self.h - 1, int(math.ceil(max(ys))))
        edges = []
        for k in range(n):
            x0, y0 = pts[k]
            x1, y1 = pts[(k + 1) % n]
            if y0 == y1:
                continue
            if y0 > y1:
                x0, y0, x1, y1 = x1, y1, x0, y0
            edges.append((y0, y1, x0, (x1 - x0) / (y1 - y0)))
        for y in range(ya, yb + 1):
            yc = y + 0.5
            xs = [x0 + (yc - y0) * s for (y0, y1, x0, s) in edges if y0 <= yc < y1]
            if len(xs) < 2:
                continue
            xs.sort()
            for k in range(0, len(xs) - 1, 2):
                self.span(y, int(math.floor(xs[k] + 0.5)), int(math.floor(xs[k + 1] + 0.5)), c, mode, a)

    def line(self, x0, y0, x1, y1, width, c: Color, mode="set", a=1.0):
        """Thick line with round caps (a capsule)."""
        dx, dy = x1 - x0, y1 - y0
        L = math.hypot(dx, dy)
        r = width / 2
        if L < 1e-6:
            return self.circle(x0, y0, r, c, mode, a)
        ang = math.atan2(dy, dx)
        pts = []
        seg = max(3, min(10, int(r / 2) + 3))
        for k in range(seg + 1):                     # cap round the end point
            t = ang - math.pi / 2 + math.pi * k / seg
            pts.append((x1 + r * math.cos(t), y1 + r * math.sin(t)))
        for k in range(seg + 1):                     # and round the start point
            t = ang + math.pi / 2 + math.pi * k / seg
            pts.append((x0 + r * math.cos(t), y0 + r * math.sin(t)))
        self.polygon(pts, c, mode, a)

    def polyline(self, pts, width, c: Color, mode="set", a=1.0, closed=False):
        n = len(pts)
        for k in range(n - (0 if closed else 1)):
            p, q = pts[k], pts[(k + 1) % n]
            self.line(p[0], p[1], q[0], q[1], width, c, mode, a)

    def ellipse(self, cx, cy, rx, ry, c: Color, mode="set", a=1.0, angle=0.0, seg=36):
        ca, sa = math.cos(angle), math.sin(angle)
        pts = []
        for k in range(seg):
            t = 2 * math.pi * k / seg
            x, y = rx * math.cos(t), ry * math.sin(t)
            pts.append((cx + x * ca - y * sa, cy + x * sa + y * ca))
        self.polygon(pts, c, mode, a)

    def star(self, cx, cy, r_out, r_in, c: Color, points=5, angle=-math.pi / 2, mode="set", a=1.0):
        pts = []
        for k in range(points * 2):
            r = r_out if k % 2 == 0 else r_in
            t = angle + math.pi * k / points
            pts.append((cx + r * math.cos(t), cy + r * math.sin(t)))
        self.polygon(pts, c, mode, a)

    def sparkle(self, cx, cy, r, c: Color, a=1.0, angle=0.0):
        """Four-point twinkle: two thin crossed diamonds and a bright core."""
        w = max(1.5, r * 0.18)
        ca, sa = math.cos(angle), math.sin(angle)
        for (ux, uy) in ((ca, sa), (-sa, ca)):
            vx, vy = -uy, ux
            self.polygon([(cx + ux * r, cy + uy * r), (cx + vx * w, cy + vy * w),
                          (cx - ux * r, cy - uy * r), (cx - vx * w, cy - vy * w)], c, "max", a)
        self.circle(cx, cy, w * 1.4, (255, 255, 255), "max", a)

    def text(self, s: str, cx, cy, height, c: Color, mode="set", a=1.0, gap=0.0):
        """Bold block letters from the app's 5-row pixel font, centred on (cx, cy)."""
        from .. import font
        bm = font.layout(s)
        if not bm.width:
            return 0.0
        cell = height / font.ROWS
        w = bm.width * cell
        x0, y0 = cx - w / 2, cy - height / 2
        pad = cell * 0.06 * gap
        for col, m in enumerate(bm.cols):
            for r in range(font.ROWS):
                if m >> r & 1:
                    self.rect(x0 + col * cell + pad, y0 + r * cell + pad,
                              x0 + (col + 1) * cell - pad, y0 + (r + 1) * cell - pad, c, mode, a)
        return w

    # ------------------------------------------------------------ out
    def crop(self, x0: int, y0: int, size: int) -> "Canvas":
        """A size x size copy starting at (x0, y0); outside the canvas reads black."""
        out = Canvas(size, size)
        W = self.w
        for k in range(3):
            src, dst = self.p[k], out.p[k]
            for y in range(size):
                sy = y0 + y
                if sy < 0 or sy >= self.h:
                    continue
                a, b = max(0, x0), min(W, x0 + size)
                if b <= a:
                    continue
                dst[y * size + (a - x0): y * size + (b - x0)] = src[sy * W + a: sy * W + b]
        return out

    def mask_circle(self, inset: float = 0.0):
        """Black outside the inscribed circle (what a round fan can't show anyway)."""
        cx, cy = self.w / 2, self.h / 2
        r = min(self.w, self.h) / 2 - inset
        for y in range(self.h):
            dy = y + 0.5 - cy
            d = r * r - dy * dy
            if d <= 0:
                self.span(y, 0, self.w, (0, 0, 0))
                continue
            hw = math.sqrt(d)
            self.span(y, 0, int(math.floor(cx - hw + 0.5)), (0, 0, 0))
            self.span(y, int(math.floor(cx + hw + 0.5)), self.w, (0, 0, 0))

    def rgb(self) -> bytes:
        """Interleaved RGB bytes, row by row."""
        out = bytearray(self.n * 3)
        out[0::3], out[1::3], out[2::3] = self.p
        return bytes(out)

    def pixel(self, x: int, y: int) -> Color:
        i = y * self.w + x
        return (self.p[0][i], self.p[1][i], self.p[2][i])
