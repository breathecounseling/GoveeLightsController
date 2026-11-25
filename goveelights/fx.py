"""Frame FX: processors that take a layer's whole frame (and their own state
from previous frames) and return a new one. Chained after a layer's
generator, like an effects rack.

    process(frame, ctx, P, state) -> frame
frame is a list of float RGB for every pixel of the virtual strip; only the
layer's target pixels (ctx.idx) matter. ctx has layout, idx, clock, dt.
"""
from __future__ import annotations

import colorsys
import math
from dataclasses import dataclass, field
from typing import Callable

from .color import BLACK, clamp, luminance, palette as sample
from .effects import Effect, Param, coord

FX: dict[str, Effect] = {}


@dataclass
class Ctx:
    layout: object
    idx: list[int]
    clock: float
    dt: float
    bpm: float = 120.0
    cache: dict = field(default_factory=dict)


def fx(name, label, params, description=""):
    def deco(fn: Callable):
        FX[name] = Effect(name, label, fn, params, description)
        return fn
    return deco


@fx("trails", "Trails", [Param("length", "number", 0.6, "Trail length (s)", 0.05, 5, 0.05)],
    "Moving light leaves a fading trail.")
def trails(frame, ctx, P, st):
    prev = st.get("prev")
    if prev is None or len(prev) != len(frame):
        st["prev"] = list(frame)
        return frame
    k = math.exp(-ctx.dt / P["length"]) if ctx.dt > 0 else 1.0
    out = list(frame)
    for i in ctx.idx:
        a, b = frame[i], prev[i]
        out[i] = (max(a[0], b[0] * k), max(a[1], b[1] * k), max(a[2], b[2] * k))
    st["prev"] = out
    return out


def _neighbours(ctx):
    """Virtual-strip neighbours within the same run."""
    key = ("nb", id(ctx.layout))
    nb = ctx.cache.get(key)
    if nb is None:
        px = ctx.layout.pixels
        nb = []
        for i, p in enumerate(px):
            left = i - 1 if i > 0 and px[i - 1].mac == p.mac else None
            right = i + 1 if i + 1 < len(px) and px[i + 1].mac == p.mac else None
            nb.append((left, right))
        ctx.cache[key] = nb
    return nb


def _blur(frame, ctx, radius):
    nb = _neighbours(ctx)
    cur = frame
    passes = max(1, int(round(radius)))
    for _ in range(passes):
        nxt = list(cur)
        for i in ctx.idx:
            l, r = nb[i]
            a = cur[l] if l is not None else cur[i]
            b = cur[r] if r is not None else cur[i]
            c = cur[i]
            nxt[i] = ((a[0] + 2 * c[0] + b[0]) / 4, (a[1] + 2 * c[1] + b[1]) / 4, (a[2] + 2 * c[2] + b[2]) / 4)
        cur = nxt
    return cur


@fx("blur", "Blur", [Param("radius", "int", 1, "Radius (bulbs)", 1, 8, 1)], "Softens edges along each run.")
def blur(frame, ctx, P, st):
    return _blur(frame, ctx, P["radius"])


@fx("glow", "Glow", [Param("radius", "int", 2, "Radius (bulbs)", 1, 8, 1),
                     Param("amount", "number", 0.8, "Amount", 0, 2, 0.05)], "Adds a halo around lit bulbs.")
def glow(frame, ctx, P, st):
    b = _blur(frame, ctx, P["radius"])
    a = P["amount"]
    out = list(frame)
    for i in ctx.idx:
        c, g = frame[i], b[i]
        out[i] = (c[0] + g[0] * a, c[1] + g[1] * a, c[2] + g[2] * a)
    return out


def _mirror_map(ctx, axis):
    key = ("mirror", axis, id(ctx.layout))
    m = ctx.cache.get(key)
    if m is not None:
        return m
    px = ctx.layout.pixels
    m = list(range(len(px)))
    if axis == "x":
        cx = (ctx.layout.minx + ctx.layout.maxx) / 2
        for i, p in enumerate(px):
            if p.x > cx:       # right half copies the nearest mirrored pixel on the left
                tx = 2 * cx - p.x
                m[i] = min(range(len(px)), key=lambda j: (px[j].x - tx) ** 2 + ((px[j].y - p.y) * 2) ** 2)
    elif axis == "run":
        for i, p in enumerate(px):
            if p.ri >= p.rn / 2:
                m[i] = i - p.ri + (p.rn - 1 - p.ri)
    else:
        n = len(px)
        for i in range(n // 2, n):
            m[i] = n - 1 - i
    ctx.cache[key] = m
    return m


@fx("mirror", "Mirror", [Param("axis", "choice", "run", options=["strip", "run", "x"],
                               help="run: each run mirrored around its middle; x: left/right across the house")],
    "Second half copies the first half, reversed.")
def mirror(frame, ctx, P, st):
    m = _mirror_map(ctx, P["axis"])
    out = list(frame)
    for i in ctx.idx:
        out[i] = frame[m[i]]
    return out


@fx("kaleido", "Kaleidoscope", [Param("segments", "int", 4, "Segments", 2, 16, 1),
                                Param("flip", "bool", True, "Flip every other")],
    "Repeats the first segment of the target along it.")
def kaleido(frame, ctx, P, st):
    idx = ctx.idx
    n = len(idx)
    seg = max(1, n // P["segments"])
    out = list(frame)
    for k, i in enumerate(idx):
        s, r = divmod(k, seg)
        if P["flip"] and s % 2:
            r = seg - 1 - r
        out[i] = frame[idx[min(r, n - 1)]]
    return out


@fx("echo", "Echo", [Param("delay", "number", 0.5, "Delay (s)", 0.05, 3, 0.05),
                     Param("mix", "number", 0.6, "Echo level", 0, 1, 0.05),
                     Param("shift", "int", 0, "Shift (bulbs)", -50, 50, 1, help="echo is offset along the strip")],
    "Adds a delayed copy of the layer.")
def echo(frame, ctx, P, st):
    hist = st.setdefault("hist", [])
    hist.append((ctx.clock, frame))
    while len(hist) > 2 and hist[1][0] <= ctx.clock - P["delay"]:
        hist.pop(0)
    old = hist[0][1]
    if len(old) != len(frame):
        return frame
    mix, sh, n = P["mix"], P["shift"], len(frame)
    out = list(frame)
    for i in ctx.idx:
        j = min(n - 1, max(0, i - sh))
        o = old[j]
        c = frame[i]
        out[i] = (max(c[0], o[0] * mix), max(c[1], o[1] * mix), max(c[2], o[2] * mix))
    return out


@fx("strobe", "Strobe", [Param("rate", "number", 4, "Flashes / s", 0.2, 25, 0.1),
                         Param("duty", "number", 0.3, "On time", 0.05, 0.95, 0.05)], "Chops the layer on and off.")
def strobe(frame, ctx, P, st):
    if (ctx.clock * P["rate"]) % 1.0 < P["duty"]:
        return frame
    out = list(frame)
    for i in ctx.idx:
        out[i] = BLACK
    return out


def _hue(c, shift, sat=1.0, val=1.0):
    h, s, v = colorsys.rgb_to_hsv(clamp(c[0]), clamp(c[1]), clamp(c[2]))
    return colorsys.hsv_to_rgb((h + shift) % 1.0, clamp(s * sat), v * val)


@fx("hue", "Hue shift", [Param("shift", "number", 0.0, "Shift", 0, 1, 0.01),
                         Param("spin", "number", 0.05, "Spin (turns/s)", -2, 2, 0.01),
                         Param("spread", "number", 0.0, "Spread along strip", 0, 3, 0.05)],
    "Rotates colours around the colour wheel.")
def hue(frame, ctx, P, st):
    base = P["shift"] + P["spin"] * ctx.clock
    sp = P["spread"]
    out = list(frame)
    for k, i in enumerate(ctx.idx):
        out[i] = _hue(frame[i], base + sp * k / max(1, len(ctx.idx)))
    return out


@fx("adjust", "Adjust", [Param("brightness", "number", 1, "Brightness", 0, 2, 0.05),
                         Param("saturation", "number", 1, "Saturation", 0, 2, 0.05),
                         Param("contrast", "number", 1, "Contrast", 0.2, 3, 0.05)], "Brightness, saturation, contrast.")
def adjust(frame, ctx, P, st):
    out = list(frame)
    b, s, ct = P["brightness"], P["saturation"], P["contrast"]
    for i in ctx.idx:
        c = frame[i]
        if s != 1:
            c = _hue(c, 0, s)
        if ct != 1:
            c = tuple(clamp(0.5 + (v - 0.5) * ct) if v > 0 else 0.0 for v in c)
        out[i] = (c[0] * b, c[1] * b, c[2] * b)
    return out


@fx("colorize", "Colorize", [Param("colors", "colors", ["#000000", "#ff2000", "#ffd000", "#ffffff"]),
                             Param("offset", "number", 0, "Offset", 0, 1, 0.01)],
    "Maps brightness onto a palette (dark → first colour, bright → last).")
def colorize(frame, ctx, P, st):
    out = list(frame)
    cols = P["colors"]
    for i in ctx.idx:
        f = clamp(luminance(frame[i]) * 1.3 + P["offset"])
        out[i] = sample(cols, f, cyclic=False)
    return out


@fx("pixelate", "Pixelate", [Param("size", "int", 4, "Block size (bulbs)", 2, 30, 1)], "Groups bulbs into blocks.")
def pixelate(frame, ctx, P, st):
    out = list(frame)
    idx, sz = ctx.idx, P["size"]
    for k in range(0, len(idx), sz):
        blk = idx[k:k + sz]
        c = frame[blk[len(blk) // 2]]
        for i in blk:
            out[i] = c
    return out


@fx("invert", "Invert", [], "Light becomes dark and dark becomes light.")
def invert(frame, ctx, P, st):
    out = list(frame)
    for i in ctx.idx:
        c = frame[i]
        out[i] = (1 - clamp(c[0]), 1 - clamp(c[1]), 1 - clamp(c[2]))
    return out


@fx("gate", "Gate", [Param("threshold", "number", 0.3, "Threshold", 0, 1, 0.01)], "Turns dim bulbs fully off.")
def gate(frame, ctx, P, st):
    out = list(frame)
    for i in ctx.idx:
        if luminance(frame[i]) < P["threshold"]:
            out[i] = BLACK
    return out


__all__ = ["FX", "Ctx", "coord"]
