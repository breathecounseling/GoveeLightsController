"""Colour helpers and deterministic noise. Colours are float RGB in 0..1."""
from __future__ import annotations

import colorsys
import math

Color = tuple[float, float, float]
BLACK: Color = (0.0, 0.0, 0.0)


def hex_to_rgb(s: str) -> Color:
    s = s.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    return (int(s[0:2], 16) / 255, int(s[2:4], 16) / 255, int(s[4:6], 16) / 255)


def rgb_to_hex(c: Color) -> str:
    return "#" + "".join(f"{round(clamp(v) * 255):02x}" for v in c)


def clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if v < lo else hi if v > hi else v


def lerp(a: Color, b: Color, f: float) -> Color:
    return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f)


def scale(c: Color, f: float) -> Color:
    return (c[0] * f, c[1] * f, c[2] * f)


def hsv(h: float, s: float = 1.0, v: float = 1.0) -> Color:
    return colorsys.hsv_to_rgb(h % 1.0, s, v)


def luminance(c: Color) -> float:
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def palette(colors: list[Color], f: float, cyclic: bool = True) -> Color:
    """Smoothly sample a list of colours at f (0..1)."""
    n = len(colors)
    if n == 1:
        return colors[0]
    if cyclic:
        x = (f % 1.0) * n
        i = int(x) % n
        return lerp(colors[i], colors[(i + 1) % n], x - int(x))
    x = clamp(f) * (n - 1)
    i = min(int(x), n - 2)
    return lerp(colors[i], colors[i + 1], x - i)


# ---------------------------------------------------------------- noise
def hash01(*ints: int) -> float:
    """Deterministic hash of integers to [0, 1)."""
    h = 0x9E3779B9
    for v in ints:
        h ^= (int(v) * 0x85EBCA6B) & 0xFFFFFFFF
        h = ((h << 13) | (h >> 19)) & 0xFFFFFFFF
        h = (h * 5 + 0xE6546B64) & 0xFFFFFFFF
    h ^= h >> 16
    h = (h * 0x85EBCA6B) & 0xFFFFFFFF
    h ^= h >> 13
    h = (h * 0xC2B2AE35) & 0xFFFFFFFF
    h ^= h >> 16
    return h / 4294967296.0


def _smooth(f: float) -> float:
    return f * f * (3 - 2 * f)


def noise2(x: float, y: float, seed: int = 0) -> float:
    """Smooth value noise in [0, 1)."""
    xi, yi = math.floor(x), math.floor(y)
    fx, fy = _smooth(x - xi), _smooth(y - yi)
    a = hash01(xi, yi, seed)
    b = hash01(xi + 1, yi, seed)
    c = hash01(xi, yi + 1, seed)
    d = hash01(xi + 1, yi + 1, seed)
    top = a + (b - a) * fx
    bot = c + (d - c) * fx
    return top + (bot - top) * fy


def fbm2(x: float, y: float, octaves: int = 3, seed: int = 0) -> float:
    total, amp, norm = 0.0, 1.0, 0.0
    for o in range(octaves):
        total += amp * noise2(x, y, seed + o * 101)
        norm += amp
        amp *= 0.5
        x *= 2.0
        y *= 2.0
    return total / norm
