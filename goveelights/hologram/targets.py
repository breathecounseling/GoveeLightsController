"""Output targets beyond round fans: the same scenes on a projector / screen, or on a
low-res LED pixel matrix (WLED and the like).

    FrameLayout(1920, 1080, background="black")   # a rectangle, any aspect
    MatrixLayout(64, 32, gamma=2.2)                # a WxH LED matrix

Both stand in for a FanLayout wherever a Clip takes a layout. Scenes were written for
round fans, so a frame is laid out as a row of "virtual fans" that cover it edge to
edge (a 16:9 frame is two overlapping circles the height of the frame): snow, stars and
the sleigh's flight then cover the whole picture rather than one circle in the middle.
Scenes that know about frames (the "Santa lands" story) use the whole rectangle.

background="black": nothing but the art is lit, which is what a projector aimed at the
house wall wants (black = no light) and what an LED matrix needs. "night" lays a deep
blue night-sky gradient under the art, for a TV or a screen in a window.

A matrix is rendered at k times its size and box-filtered down (every output pixel is
the average of the k x k block it covers), so thin strokes become dimmer pixels rather
than vanishing or flickering. gamma > 1 then darkens the mid-tones the way LEDs need
(WLED's own "gamma correct" does the same; use one or the other).
"""
from __future__ import annotations

import math

from .canvas import Canvas, clamp8

FRAME_MIN, FRAME_MAX = 64, 3840
FRAME_MAX_PIXELS = 3840 * 2160
MATRIX_MIN, MATRIX_MAX = 8, 256
MATRIX_MAX_PIXELS = 128 * 128
BACKGROUNDS = ("black", "night")

# the night sky under the art (top colour, bottom colour) for background="night"
NIGHT_TOP, NIGHT_BOTTOM = (2, 4, 18), (14, 26, 64)


def parse_wh(text: str, what: str = "size") -> tuple[int, int]:
    """'1920x1080' (or 1920*1080, 1920,1080) -> (1920, 1080)."""
    s = str(text or "").lower().replace("*", "x").replace(",", "x").replace("×", "x").strip()
    parts = [p.strip() for p in s.split("x")]
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        raise ValueError(f"{what} must be WIDTHxHEIGHT, e.g. 1920x1080")
    return int(parts[0]), int(parts[1])


def parse_frame(text: str, height: int = 720) -> tuple[int, int]:
    """A frame size: 'WxH', or an aspect ratio 'A:B' at the given height (16:9 -> 1280x720)."""
    s = str(text or "").strip()
    if ":" in s:
        a, _, b = s.partition(":")
        try:
            a, b = float(a), float(b)
        except ValueError:
            raise ValueError("aspect must look like 16:9") from None
        if not (a > 0 and b > 0 and 0.2 <= a / b <= 5):
            raise ValueError("aspect must be between 1:5 and 5:1")
        h = int(height)
        w = int(round(h * a / b / 2)) * 2
        return w, h
    return parse_wh(s, "frame")


def _virtual_fans(w: int, h: int) -> tuple[int, list[tuple[float, float]]]:
    """Fan diameter and centres of the circles that cover a w x h rectangle."""
    d = min(w, h)
    if w >= h:
        n = max(1, int(round(w / h)))
        if n == 1:
            return d, [(w / 2, h / 2)]
        p = (w - d) / (n - 1)
        return d, [(d / 2 + k * p, h / 2) for k in range(n)]
    n = max(1, int(round(h / w)))
    if n == 1:
        return d, [(w / 2, h / 2)]
    p = (h - d) / (n - 1)
    return d, [(w / 2, d / 2 + k * p) for k in range(n)]


class FrameLayout:
    """A rectangular picture (projector, TV, video wall): stands in for a FanLayout."""

    kind = "frame"
    name = "frame"
    rows = 1

    def __init__(self, w: int, h: int, background: str = "black"):
        self.w, self.h = int(w), int(h)
        if not (FRAME_MIN <= self.w <= FRAME_MAX and FRAME_MIN <= self.h <= FRAME_MAX):
            raise ValueError(f"frame sides must be {FRAME_MIN} to {FRAME_MAX} pixels")
        if self.w * self.h > FRAME_MAX_PIXELS:
            raise ValueError("frame is bigger than 4K (3840x2160)")
        if background not in BACKGROUNDS:
            raise ValueError(f"background must be one of {', '.join(BACKGROUNDS)}")
        self.background = background
        self.size, self._centres = _virtual_fans(self.w, self.h)
        self.cols = len(self._centres)

    # --- what a scene's Ctx reads (the same as FanLayout)
    @property
    def count(self) -> int:
        return 1                                     # one output picture

    @property
    def canvas_size(self) -> tuple[int, int]:
        return (self.w, self.h)

    @property
    def out_size(self) -> tuple[int, int]:
        return (self.w, self.h)

    def centres(self) -> list[tuple[float, float]]:
        return list(self._centres)

    def origins(self) -> list[tuple[int, int]]:
        return [(0, 0)]

    def hero(self) -> int:
        return (len(self._centres) - 1) // 2

    def finish(self, cv: Canvas) -> Canvas:
        """The picture as shown: the art, over a night sky if asked."""
        if self.background == "night":
            night_sky(cv)
        return cv

    def tiles(self, cv: Canvas, mask: bool = True) -> list[Canvas]:
        return [self.finish(cv)]

    def file_stem(self, scene: str) -> str:
        bg = "" if self.background == "black" else "-" + self.background
        return f"{scene}-frame-{self.w}x{self.h}{bg}"

    def to_dict(self) -> dict:
        return {"name": self.name, "kind": self.kind, "w": self.w, "h": self.h, "background": self.background,
                "canvas": [self.w, self.h], "size": self.size, "origins": [(0, 0)], "rows": 1, "cols": 1}


def night_sky(cv: Canvas):
    """Lighten the frame up to a deep-blue gradient (the art stays on top: 'max' blend)."""
    h = cv.h
    for y in range(h):
        f = y / max(1, h - 1)
        c = tuple(clamp8(a + (b - a) * f) for a, b in zip(NIGHT_TOP, NIGHT_BOTTOM))
        cv.span(y, 0, cv.w, c, "max")


def gamma_table(g: float) -> bytes:
    if abs(g - 1.0) < 1e-6:
        return bytes(range(256))
    return bytes(clamp8(255 * (v / 255) ** g) for v in range(256))


def box_down(src: Canvas, k: int, out: Canvas | None = None) -> Canvas:
    """Average every k x k block of src into one pixel (area-averaged downsampling).
    Sums run as 16-bit lanes of big integers (k <= 16, so 255 * k * k fits)."""
    if not 1 <= k <= 16:
        raise ValueError("downsampling factor must be 1 to 16")
    ow, oh = src.w // k, src.h // k
    if out is None:
        out = Canvas(ow, oh)
    if k == 1:
        for c in range(3):
            out.p[c][:] = src.p[c][:ow * oh]
        return out
    W = src.w
    nb = W * 2
    n = k * k
    half = n // 2
    div = [(v + half) // n for v in range(255 * n + 1)]
    for c in range(3):
        wide = bytearray(2 * src.n)                        # one little-endian 16-bit lane per pixel
        wide[0::2] = src.p[c]
        dst = out.p[c]
        for r in range(oh):
            acc = 0
            base = r * k
            for j in range(k):                             # add k rows, lane by lane
                y = base + j
                acc += int.from_bytes(wide[y * nb:(y + 1) * nb], "little")
            row = acc.to_bytes(nb, "little")
            acc2 = 0
            lane = bytearray(ow * 2)
            for j in range(k):                             # then k neighbouring columns
                lane[0::2] = row[2 * j:2 * ow * k:2 * k]
                lane[1::2] = row[2 * j + 1:2 * ow * k:2 * k]
                acc2 += int.from_bytes(lane, "little")
            sums = acc2.to_bytes(ow * 2, "little")
            dst[r * ow:(r + 1) * ow] = bytes(div[lo | hi << 8] for lo, hi in zip(sums[0::2], sums[1::2]))
    return out


class MatrixLayout:
    """A W x H LED pixel matrix (WLED 2D, a P10 panel...): stands in for a FanLayout.
    The scene is drawn on a k-times-bigger frame, then averaged down."""

    kind = "matrix"
    name = "matrix"
    rows = 1

    def __init__(self, w: int, h: int, gamma: float = 1.0, serpentine: bool = False, background: str = "black"):
        self.w, self.h = int(w), int(h)
        if not (MATRIX_MIN <= self.w <= MATRIX_MAX and MATRIX_MIN <= self.h <= MATRIX_MAX):
            raise ValueError(f"matrix sides must be {MATRIX_MIN} to {MATRIX_MAX} pixels")
        if self.w * self.h > MATRIX_MAX_PIXELS:
            raise ValueError(f"matrix is more than {MATRIX_MAX_PIXELS} pixels")
        self.gamma = float(gamma)
        if not 0.5 <= self.gamma <= 3.5:
            raise ValueError("gamma must be 0.5 to 3.5")
        self.serpentine = bool(serpentine)
        # render at least ~384 px across (and 96 tall), at most 16x (the box filter's limit)
        self.k = max(2, min(16, int(math.ceil(max(384 / self.w, 96 / self.h)))))
        # ...but the render frame must stay within a frame's limits (8x256: 16x would be 4096 tall)
        while self.k > 2 and (max(self.w, self.h) * self.k > FRAME_MAX
                              or self.w * self.h * self.k * self.k > FRAME_MAX_PIXELS):
            self.k -= 1
        self.frame = FrameLayout(max(FRAME_MIN, self.w * self.k), max(FRAME_MIN, self.h * self.k), background)
        if (self.frame.w, self.frame.h) != (self.w * self.k, self.h * self.k):     # tiny matrices: keep it exact
            self.k = max(math.ceil(FRAME_MIN / self.w), math.ceil(FRAME_MIN / self.h))
            if self.k > 16:
                raise ValueError("matrix too small")
            self.frame = FrameLayout(max(FRAME_MIN, self.w * self.k), max(FRAME_MIN, self.h * self.k), background)
        self.size = self.frame.size
        self.cols = self.frame.cols
        self._gt = gamma_table(self.gamma)
        self._out = None

    @property
    def count(self) -> int:
        return 1

    @property
    def canvas_size(self) -> tuple[int, int]:
        return self.frame.canvas_size

    @property
    def out_size(self) -> tuple[int, int]:
        return (self.w, self.h)

    def centres(self):
        return self.frame.centres()

    def origins(self):
        return [(0, 0)]

    def hero(self) -> int:
        return self.frame.hero()

    def tiles(self, cv: Canvas, mask: bool = True) -> list[Canvas]:
        cv = self.frame.finish(cv)
        if (cv.w, cv.h) != (self.w * self.k, self.h * self.k):       # crop the render to an exact multiple
            cv = _crop(cv, self.w * self.k, self.h * self.k)
        out = box_down(cv, self.k, Canvas(self.w, self.h))
        if self._gt != bytes(range(256)):
            for c in range(3):
                out.p[c][:] = out.p[c].translate(self._gt)
        return [out]

    def wire_order(self, tile: Canvas) -> bytes:
        """RGB bytes in the order the LEDs are wired: row by row from the top left, or
        (serpentine) every other row right to left."""
        rgb = tile.rgb()
        if not self.serpentine:
            return rgb
        w3 = self.w * 3
        out = bytearray(rgb)
        for y in range(1, self.h, 2):
            row = rgb[y * w3:(y + 1) * w3]
            px = [row[x * 3:x * 3 + 3] for x in range(self.w)]
            out[y * w3:(y + 1) * w3] = b"".join(reversed(px))
        return bytes(out)

    def file_stem(self, scene: str) -> str:
        return f"{scene}-matrix-{self.w}x{self.h}"

    def to_dict(self) -> dict:
        return {"name": self.name, "kind": self.kind, "w": self.w, "h": self.h, "gamma": self.gamma,
                "serpentine": self.serpentine, "render": [self.frame.w, self.frame.h], "k": self.k,
                "canvas": [self.w, self.h], "size": self.size, "origins": [(0, 0)], "rows": 1, "cols": 1}


def _crop(cv: Canvas, w: int, h: int) -> Canvas:
    out = Canvas(w, h)
    for c in range(3):
        src, dst = cv.p[c], out.p[c]
        for y in range(h):
            dst[y * w:(y + 1) * w] = src[y * cv.w:y * cv.w + w]
    return out


def led_view(tile: Canvas, scale: int = 10, gap: int = 2) -> Canvas:
    """A matrix frame blown up so it looks like the LEDs: square dots on black."""
    s, g = int(scale), max(0, min(int(gap), int(scale) - 1))
    lit = s - g
    W, H = tile.w * s, tile.h * s
    out = Canvas(W, H)
    blank = bytes(W)
    for c in range(3):
        src, dst = tile.p[c], out.p[c]
        for y in range(tile.h):
            row = src[y * tile.w:(y + 1) * tile.w]
            line = b"".join(bytes((v,)) * lit + b"\0" * g for v in row)
            for j in range(s):
                o = (y * s + j) * W
                dst[o:o + W] = line if j < lit else blank
    return out


def ansi(tile: Canvas, max_cols: int = 96) -> str:
    """A frame as terminal text: two pixels per character cell (upper half block), 24-bit
    colour. Bigger frames are averaged down to fit max_cols."""
    while tile.w > max_cols:                   # in steps of at most 16 (the box filter's limit)
        k = min(16, -(-tile.w // max_cols))
        tile = box_down(_crop(tile, tile.w // k * k, tile.h // k * k), k)
    lines = []
    w = tile.w
    for y in range(0, tile.h - 1, 2):
        parts = []
        for x in range(w):
            a = tile.pixel(x, y)
            b = tile.pixel(x, y + 1)
            parts.append(f"\x1b[38;2;{a[0]};{a[1]};{a[2]}m\x1b[48;2;{b[0]};{b[1]};{b[2]}m▀")
        lines.append("".join(parts) + "\x1b[0m")
    return "\n".join(lines)
